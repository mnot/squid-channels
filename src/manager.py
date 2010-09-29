#!/usr/bin/env python

"""
Cache Channel Manager/Handler
by Mark Nottingham <mnot@yahoo-inc.com>.

This program is designed to run as a single external_refresh_check handler
 for Squid; the configuration line MUST look like this:
  external_refresh_check children=1 concurrency=99 %CACHE_URI %AGE %RES{Cache-Control} %RES{Link} /path/to/this/program /path/to/conf/file

In particular, children MUST NOT be more than "1".

It requires Python 2.3+ and the following additional libraries:
 - Twisted <http://twistedmatrix.com/>
 - Dateutil <http://labix.org/python-dateutil>

See the sample configuration file for details of its content.

It's a really good idea to configure a HTTP proxy, because;
 - This process doesn't use persistent connections, whereas most HTTP 
   proxies (e.g., squid on localhost) will.
 - The only state that's kept between invocations is the list of channels 
   being monitored, not their contents; using a caching proxy will make 
   startup and restarts more efficient (so that the entire channel 
   contents don't have to be re-read across the network).
 - If your cache is tied together with others (e.g., using ICP), it will 
   reduce the load on the server that hosts the channel.
 - DNS in twisted is synchronous (?); using a proxy will offload any potential 
   blocking to another process.

Usually, this means pointing this program at the Squid that it's managing 
channels for. However, if it's set up as an accelerator, you can either 
a) not configure a proxy at all, if the channel is being hosted on the 
site being accelerated, or b) deploy a separate, small proxy just for 
this purpose nearby.

To take full advantage of this, make sure that your channels' feed 
documents are cacheable.

Note that logging is synchronous (because it writes to disk), and 
therefore can slow down this process. As a result, we use the response 
to Squid to log most runtime events; debugging is log-intensive and 
should not be turned on in production.
  
Maintainers: Note that only one instance runs per Squid process, and that 
it can handle concurrent requests. It is an asynchronous process (i.e., 
it does not use threads), so code in this process MUST NOT block.
"""

__copyright__ = """\
Copyright (c) 2007-2010 Yahoo! Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


  
import os
import sys
import re
import time
import calendar
import logging
import ConfigParser
import xml.dom.minidom
from urllib import unquote
from urlparse import urljoin
from xml.parsers import expat
from logging.handlers import RotatingFileHandler
from twisted.internet import reactor, stdio
from twisted.protocols.basic import LineReceiver
from twisted.internet import error as internet_error
from twisted.web import client
from twisted.web import error as web_error
from dateutil import parser  # http://labix.org/python-dateutil

# FIXME: incompatible with stale-while-revalidate?
# TODO: HTTP authentication, validation
# TODO: GC old feeds?
# TODO: hash URIs for space?

class ChannelManager:
    def __init__(self, reactor_, config):
        self.reactor = reactor_
        self.config = config
        self.channels = {}
        self.error_check = 30  # how often to look to see if an error has cleared (seconds)
        self.gc_period = 60 * 60 * 24  # how often to garbage collect old entries
        self.min_check_time = 5  # minimum number of seconds between checks

    def start(self):
        logging.info("start_manager")
        try:
            db = open(self.config.get("main", "dbfile"), 'r')
            for line in db:
                self.add_channel(line.strip())
            db.close()
        except IOError, why:
            logging.info("db_read_error (%s)" % why)
        self.reactor.callLater(self.gc_period, self._gc)            
        self.reactor.run()

    def shutdown(self):
        logging.info("stop_manager")
        self.reactor.stop()
        try:
            db = open(self.config.get("main", "dbfile"), 'w')
            for channel_uri in self.channels.keys():
                db.write("%s\n" % channel_uri)
            db.close()
        except IOError, why:
            logging.critical("db_write_error (%s)" % why)
                        
    def add_channel(self, channel_uri):
        if not channel_uri in self.channels.keys():
            self.channels[channel_uri] = {'uri': channel_uri}
            self._schedule_check(channel_uri)
            logging.info("new_channel_added <%s>" % channel_uri)
                
    def _schedule_check(self, channel_uri, when=0):
        def check():
            channel = self.channels[channel_uri]
            c = AtomChannel(
                channel, self._check_done, self._check_error, self.config
            )
            logging.debug("checking <%s>" % channel_uri)
            c.check()
        logging.debug("schedule_check <%s> %2.2f" % (channel_uri, when))
        self.reactor.callLater(when, check)

    def _check_done(self, channel):
        now = time.time()
        channel['last_check'] = now
        self.channels[channel['uri']] = channel
        logging.debug("check_done <%s> %2.2f" % (
            channel['uri'], channel['last_check_elapsed']
        ))
        wait = max(
            (channel['precision'] - channel.get('last_check_elapsed', 0)) * \
            ((100 - self.config.getint('main', 'extend_pct')) / 100.0 ),
            self.min_check_time
        )
        if wait == self.min_check_time:
            logging.warning("check_delay <%s>; using min_check_time" % \
                channel['uri']
            )
        self._schedule_check(channel['uri'], wait)

    def _check_error(self, channel, message=""):
        logging.warning("check_error <%s> %s" % (channel['uri'], message))
        self._schedule_check(
            channel['uri'], 
            channel.get('precision', self.error_check )
        ) # TODO: back-off algorithm

    def _gc(self):
        logging.info("garbage_collection")
        now = time.time()
        for ck in self.channels:
            lifetime = self.channels[ck].get('lifetime', None)
            if not lifetime:
                logging.info("no_lifetime <%s>" % self.channels[ck]['uri'])
                continue
            for k, v in self.channels[ck]['events'].items():
                if v < (now - lifetime):
                    del self.channels[ck]['events'][k]
                    logging.debug("gc_event <%s> <%s> %s" % (ck, k, v))            
        self.reactor.callLater(self.gc_period, self._gc)


class AtomChannel:
    def __init__(self, channel, done_cb, error_cb, config):
        self.channel = channel
        self.done_cb = done_cb
        self.error_cb = error_cb
        self.config = config
        self.default_precision = 60
        self.default_lifetime = 604800
        self.archives_requested = []
        self.archives_seen = []
        self.start_time = None
        
    def check(self):
        self.start_time = time.time()
        self.fetch(self.channel['uri'], self.process_sub_doc, 
           req_headers={"Cache-Control": "max-age=%s" % \
                self.channel.get('precision', self.default_precision)})        
                
    def process_sub_doc(self, uri, instr):
        head_links, md, events = parse_feed(uri, instr)
        self.channel['lifetime'] = int(
            md["lifetime"] or self.default_lifetime
        )
        self.channel['precision'] = int(
            md["precision"] or self.default_precision
        )
        self.add_events(events)
        self.check_prev(head_links)

    def check_prev(self, head_links):
        prev_uri = head_links.get('prev-archive', None)
        logging.debug("prev_uri <%s>" % prev_uri or "")
        if prev_uri and prev_uri != \
        self.channel.get('last_archive_seen', None):
            self.archives_requested.append(prev_uri)
            logging.debug("checking prev_uri")
            self.fetch(prev_uri, self.process_archive, req_headers={
                "Cache-Control": "max-stale=%s" % self.channel['lifetime']
            })
        else:
            self.done()
    
    def process_archive(self, uri, instr):
        head_links, md, events = parse_feed(uri, instr)
        self.add_events(events)
        self.archives_seen.append(uri)
        self.check_prev(head_links)

    def add_events(self, events):
        if not self.channel.has_key('events'):
            logging.debug("empty_event_dict <%s>" % self.channel['uri'])
            self.channel['events'] = {}
        for uri, date in events:
            if date is None:
                logging.warning("bad_event_date <%s> <%s>" % \
                    (self.channel['uri'], uri)
                )
                date = time.time()
            if date <= self.channel['events'].get(uri, 0):
                continue
            self.channel['events'][uri] = date
            logging.debug("add_event <%s> <%s> %s" % (
                self.channel['uri'], uri, date
            ))

    def done(self):
        self.archives_requested.reverse()
        for archive_uri in self.archives_requested:
            if archive_uri in self.archives_seen:
                self.channel['last_archive_seen'] = archive_uri
            else:
                break
        self.channel['last_check_elapsed'] = time.time() - self.start_time
        self.done_cb(self.channel)

    def fetch(self, uri, cb, req_headers=None):
        def callback(data):
            cb(uri, data)
        def errback(data):
            if data.type == web_error.Error:
                msg = '"%s"' % data.value
            elif data.type == internet_error.DNSLookupError:
                msg = '"DNS lookup error"'
            elif data.type == internet_error.TimeoutError:
                msg = '"Timeout"'
            elif data.type == internet_error.ConnectionRefusedError:
                msg = '"Connection refused"'
            elif data.type == internet_error.ConnectError:
                msg = '"Connection error"'
            elif data.type == expat.ExpatError:
                msg = '"XML parsing error (%s)"' % data.value
            else:
                msg = '"Unknown error (%s)"' % data.type
            self.error_cb(self.channel, msg)
        c = getPage(str(uri), 
                timeout=self.config.getint("main", "fetch_timeout"), 
                proxy=self.config.get("main", "http_proxy"),
                headers=req_headers
        )
        c.addCallback(callback)
        c.addErrback(errback)


class SquidHandlerProtocol(LineReceiver):
    delimiter = '\n'
    clock_fuzz = 5

    def __init__(self, manager):
        self.manager = manager
        
    def lineReceived(self, line):
        line = line.rstrip()
        logging.debug('handler_request %s' % line)
        result = self.process(line)
        sys.stdout.write("%s\n" % result)
        sys.stdout.flush()
        logging.debug("handler_response %s" % result)

    def connectionLost(self, reason):
        self.manager.shutdown()

    def process(self, line):
        try:
            req_id, request_uri, age, cc_str, link_str = line.split(None, 4)
            age = int(age)
            cc = parse_cc(unquote(cc_str))
            links = parse_link(unquote(link_str))
        except (IndexError, ValueError): 
            return "%s STALE log=malformed_line_error" % line.split(None)[0]
        STALE = "%s STALE log=%%s" % req_id
        channel_maxage = cc.get('channel-maxage', None)
        if channel_maxage is None:
            return STALE % "no_channel_maxage"
        channel_uri = cc.get('channel', None)
        if channel_uri is None:
            return STALE % "no_channel_advertised"
        channel_uri = urljoin(request_uri, channel_uri)
        try:
            channel = self.manager.channels[channel_uri]
        except KeyError:
            self.manager.add_channel(channel_uri)
            return STALE % "channel_not_monitored"
        if not channel.has_key('last_check'):
            return STALE % "channel_startup"
        now = time.time() 
        if now > channel['last_check'] + channel['precision']:
            return STALE % "channel_dead"
        events = channel.get('events', {})
        response_cached = now - age - self.clock_fuzz
        if events.has_key(request_uri) and \
        events[request_uri] > response_cached:
            return STALE % "invalidated_request_uri"
        for group_uri, params in links.items():
            if params.get('rev', "").lower() != 'invalidates':
                continue
            group_uri = urljoin(request_uri, group_uri)
            logging.debug("group_uri <%s> rev %s" % (
                group_uri, params['rev']
            ))
            if events.has_key(group_uri) and \
            events[group_uri] > response_cached:
                return STALE % "invalidated_group_uri"
        if channel_maxage != True:
            try:
                channel_maxage = int(channel_maxage)
            except ValueError:
                return STALE % "invalid_channel_maxage"
            if age > channel_maxage:
                return STALE % "channel_maxage"
        if age > channel['lifetime']:
            return STALE % "channel_lifetime"
        extend_by = channel['precision'] * (
            self.manager.config.getint('main', 'extend_pct') / 100.0
        )
        date = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now))
        return "%s FRESH freshness=%s res{Date}=\"%s\" log=extended_%2.2f" % \
            (req_id, extend_by, date, extend_by)


def _unquotestring(instr):
    if instr[0] == instr[-1] == '"':
        instr = instr[1:-1]
        instr = re.sub(r'\\(.)', r'\1', instr)
    return instr

def _splitstring(instr, item, split):
    if not instr: 
        return []
    return [ h.strip() for h in 
             re.findall(r'%s(?=%s|\s*$)' % (item, split), instr)
           ]    
    
## Cache-Control header parsing
TOKEN = r'(?:[^\(\)<>@,;:\\"/\[\]\?={} \t]+?)'
QUOTED_STRING = r'(?:"(?:\\"|[^"])*")'
PARAMETER = r'(?:%(TOKEN)s(?:=(?:%(TOKEN)s|%(QUOTED_STRING)s))?)' % locals()
COMMA = r'(?:\s*(?:,\s*)+)'
PARAM_SPLIT = r'%s(?=%s|\s*$)' % (PARAMETER, COMMA)
param_splitter = re.compile(PARAM_SPLIT)
def parse_cc(instr, force_list=None):
    out = {}
    if not instr: 
        return out
    for param in [h.strip() for h in param_splitter.findall(instr)]:
        try:
            attr, value = param.split("=", 1)
            value = _unquotestring(value)
        except ValueError:
            attr = param
            value = True
        attr = attr.lower()
        if force_list and attr in force_list:
            if out.has_key(attr):
                out[attr].append(value)
            else:
                out[attr] = [value]
        else:
            out[attr] = value
    return out

LINK = r'<[^>]*>\s*(?:;\s*%(PARAMETER)s?\s*)*' % locals()
LINK_SPLIT = r'%s(?=%s|\s*$)' % (LINK, COMMA)
link_splitter = re.compile(LINK_SPLIT)
def parse_link(instr):
    out = {}
    if not instr: 
        return out
    for link in [h.strip() for h in link_splitter.findall(instr)]:
        url, params = link.split(">", 1)
        url = url[1:]
        param_dict = {}
        for param in _splitstring(params, PARAMETER, "\s*;\s*"):
            try:
                a, v = param.split("=", 1)
                param_dict[a.lower()] = _unquotestring(v)
            except ValueError:
                param_dict[param.lower()] = None
        out[url] = param_dict
    return out

def error(msg):
    logging.critical(msg)
    sys.stderr.write("FATAL: %s\n" % msg)
    sys.exit(1)

def main(configfile):        
    # load config
    try:
        config = ConfigParser.SafeConfigParser(
           {
            'pidfile': None, 
            'http_proxy': None, 
            'extend_pct': "33", 
            'log_level': logging.INFO, 
            'fetch_timeout': "10",
            'log_backup': "5",
            }
        )
        config.read(configfile)
        pidfile = config.get("main", "pidfile")
        logfile = config.get("main", "logfile")
        log_level = config.get("main", "log_level").strip().upper()
        log_backup = config.getint("main", "log_backup")        
    except ConfigParser.Error, why:
        error("Configuration file: %s\n" % why)
            
    # logging
    logger = logging.getLogger()
    try:
        hdlr = RotatingFileHandler(
            logfile, maxBytes=1024 * 1024 * 10, backupCount=log_backup
        )
    except IOError, why:
        error("Can't open log file (%s)" % why)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr) 
    logger.setLevel(logging._levelNames.get(log_level, logging.INFO))

    # PID management
    if pidfile:
        if os.path.exists(pidfile):
            error("Channel manager already running (PID %s)." % \
                open(pidfile).read()
            )
        try:
            pid_fh = open(pidfile, 'w')
            pid_fh.write(str(os.getpid()))
            pid_fh.close()
        except IOError, why:
            error("Can't write PID file (%s)." % why)
    
    # run
    try:
        try:
            cm = ChannelManager(reactor, config)
            stdio.StandardIO(SquidHandlerProtocol(cm))
            cm.start()
        except IOError, why:
            error(why)
        except ConfigParser.Error, why:
            error("Configuration file: %s\n" % why)
    finally:
        if pidfile:
            try:
                os.unlink(pidfile)
            except OSError, why:
                error("Can't remove PID file (%s)." % why)


######## feed parser

FH = "http://purl.org/syndication/history/1.0"
RSS1 = "http://purl.org/rss/1.0/"
ATOM = "http://www.w3.org/2005/Atom"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CC = "http://purl.org/syndication/cache-channel"

# TODO: move from DOM to SAX.
# TODO: work with more events than just cc:stale

def parse_feed(uri, instr):
    def get_links(base_uri, node):
        if not node: 
            return {}
        return dict([
            (i.getAttribute("rel") or "alternate", 
             urljoin(base_uri, i.getAttribute("href"))
            ) for i in node if (
             i.namespaceURI == ATOM and i.localName == "link" 
            )
        ])
    dom = xml.dom.minidom.parseString(instr)
    if dom.documentElement.namespaceURI != ATOM or \
    dom.documentElement.localName != u'feed':
        raise NotImplementedError, "Feed Format Not Recognized"
        
    head = [i for i in dom.documentElement.childNodes if not (i.namespaceURI != ATOM and i.localName == "entry")]
    entries = [i for i in dom.documentElement.childNodes if (i.namespaceURI == ATOM and i.localName == "entry")]
    head_links = get_links(uri, head)
    md = {
      "precision": ([i.childNodes[0].data.strip() for i in head if i.namespaceURI == CC and i.localName == "precision"][:1] or [None])[0],
      "lifetime": ([i.childNodes[0].data.strip() for i in head if i.namespaceURI == CC and i.localName == "lifetime"][:1] or [None])[0],
      "archive_num": ([i.childNodes[0].data.strip() for i in head if i.namespaceURI == CC and i.localName == "archive_num"][:1] or [0])[0],
    }
    events = []
    for entry in entries:
        if entry.getElementsByTagNameNS(CC, 'stale') == []:
            continue # only interested in stale events for now
        entry_uri = get_links(uri, entry.childNodes)['alternate']
        updated_str = entry.getElementsByTagNameNS(
            ATOM, "updated")[0].firstChild.data.strip()
        if updated_str:
            updated = calendar.timegm(
                parser.parse(updated_str).utctimetuple()
            )   #IGNORE:E1103
        else:
            updated = None
        events.append((entry_uri, updated))
    return head_links, md, events


## twisted getPage with proxy
def getPage(url, contextFactory=None, proxy=None, *args, **kwargs):
    scheme, host, port, path = client._parse(url)
    if proxy:
        host, port = proxy.split(':')
        port = int(port)
        kwargs['proxy'] = proxy
    factory = HTTPClientFactory(url, *args, **kwargs)
    reactor.connectTCP(host, port, factory)  #IGNORE:E1101
    return factory.deferred

class HTTPClientFactory(client.HTTPClientFactory):
    def __init__(self, *args, **kwargs):
        self.proxy = kwargs.get('proxy', None)
        if self.proxy != None:            
            del kwargs['proxy']
        client.HTTPClientFactory.__init__(self, *args, **kwargs)
        
    def setURL(self, url):
        self.url = url
        scheme, host, port, path = client._parse(url)
        if scheme and host:
            self.scheme = scheme
            self.host = host
            self.port = port
        if self.proxy:
            self.path = "%s://%s:%s%s" % (self.scheme,  
                                          self.host,  
                                          self.port,  
                                          path)
        else:
            self.path = path
        
if __name__ == '__main__':
    try:
        conf = sys.argv[1]
    except IndexError:
        sys.stderr.write("USAGE: %s config_filename\n" % sys.argv[0]) 
        sys.exit(1)
    if not os.path.exists(conf):
        error("Can't find config file %s." % conf)
    main(conf)

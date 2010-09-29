Cache Channels for Squid
========================

Copyright (c) 2007-2010 Yahoo! Inc.
See src/manager.py for license.


What are Cache Channels?
------------------------

A Cache Channel is an out-of-band communication path between Web
servers and Web caches. When a cache is subscribed to a channel, it
can find out things about the content it has stored from the origin
server and act on it, significantly increasing its efficiency, and
giving the publisher finer-grained control.

A channel is associated with content by sending a HTTP header
containing the channel's URI along with the response. For example::

  Cache-Control: max-age=30, channel="http://example.com/channel/index.atom"

When the cache sees this, it knows that it should subscribe to that
channel and listen for events.

Neither the protocol used for the channel nor the types of events
it can carry are fixed by the specification. See the
`Internet-Draft <http://datatracker.ietf.org/doc/draft-nottingham-http-cache-channels/>`_
for more information.

What can they do for me?
------------------------

Currently, there is one application of cache channels; when a
response also says ``channel-maxage``, it allows a cache that is
subscribed to the indicated channel to indefinitely extend the
freshness lifetime of that response until the channel says
otherwise, with an applicable ``stale`` event.

This is useful in situations where you want to keep tight control
over the freshness of responses served, but still want the
efficiency boost of caching. Instead of trading off the control of
a short max-age against the efficiency of a long one, you can have
the best of both worlds.

Additionally, cache channels allows you to make responses stale not
only by their request-URI, but also by their *group*. Since a group
URI can be associated with many responses, this allows many cached
responses to be marked stale with just one invalidation event.
Additionally, since it's not necessary to track all of the
request-URIs that are associated with one concept, it offers a
powerful way to manage cached search results (for example).

Example: Using Channels with an HTTP Accelerator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Imagine that you have a Web server, ``http://example.com/`` with
Squid in front of it as an accelerator, and you're serving
thousands of different URLs through it, including static images,
HTML pages, and search results.

Since you're using a cache in front of the server, you've set the
``Cache-Control: max-age`` header, so that you control how often
the cache checks to see if something has changed::

  Cache-Control: max-age=300

However, you find that doing so is a trade-off; if you make
``max-age`` too large, you lose control over your content, and if
you make it too small, the cache doesn't add a lot of efficiency.
Many sites have business or legal requirements for how quickly
changes need to be reflected on the 'live' site, so usually a
compromise is made and this number is quite small (in the example
above, five minutes).

Cache Channels provide another option. By associating all of the
content on the site with a single channel, you can increase the
cacheability of the site's responses while keeping control of them.
That's done by adding a ``channel`` Cache-Control directive to each
of the responses that you want covered by that channel::

  Cache-Control: max-age=300, channel="http://example.com/channel/index.atom", channel-maxage

Now, the accelerator can monitor that channel for *stale events*,
so that it doesn't have to check back with the server every time a
response becomes stale in its cache. Every time something changes
on your server, you'll need to push an event into the channel to
notify the subscribing caches that something has become stale.

Caches subscribed to the channel will know about this in a bounded amount
of time, called the channel's ``precision`` (by default, one minute).

Group Invalidation
~~~~~~~~~~~~~~~~~~

The approach outlined above works when you know the URL of what you
want to invalidate, but what if you don't? For example, if you're
caching the results of a query interface, one piece of information
might be included in a number of different responses. All of the
following search URLs may become invalid when something happens to
Paris Hilton::

  http://example.com/query?t=Paris
  http://example.com/query?t=Bimbo
  http://example.com/query?t=Hilton

Storing these URIs so that you can send events about them later is
impractical. Instead, there's another option; ``groups``. By
associating a single group with all of these query responses,
they're tied together with one "synthetic" URI::

  Cache-Control: max-age=300, channel="http://example.com/channel/index.atom", 
    channel-maxage, group="/paris-hilton"

so that you can mark them stale in one go, by marking

  http://example.com/paris-hilton

as stale.

You can also associate a number of groups (within reason) with a
single response. For example, the "Hilton" query might need to be
associated with a few::

  Cache-Control: max-age=300, channel="http://example.com/channel/index.atom", 
    channel-maxage, group="/paris-hilton", group="/hilton-hotels"

What are their limitations?
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Cache channels make several trade-offs to assure that the don't
unduly load the network, offer reasonable guarantees about
operation, and handle failure modes appropriately. Please keep the
following in mind with using them;

-  This implementation uses HTTP polling of the channel to discover
   new events. This scales well, in that as long as the channel
   representations are cacheable for an appropriate amount of time
   (e.g., 15 seconds), many caches can share the same responses.
   However, if the polling interval is too frequent, the benefits of
   using channels may be lost due to the increased network traffic and
   server load.
-  The maximum size of a HTTP response header is limited in most
   implementations, and as a result the number of group-URIs
   associated with a response cannot be too large. Practically,
   headers should not be larger than 2K.
-  Channel events are not propagated instantly. However, the system
   does guarantee that an event will be propagated within the
   ``precision`` period of time indicated by the channel (by default,
   30 seconds).
-  Channel events are not synchronised across multiple caches;
   however, if the caches are tied together using ICP, or use the same
   parent, events should be applied closely together.
-  In the event that the channel goes down, the system will default
   back to using the normal cacheability information in responses.
   This may substantially increase the load on your origin servers.

How do I use them?
------------------

This implementation currently supports only Atom channels and
"stale" events, respectively. Together, they can be used to offer a
new way of controlling cache freshness to origin servers.

When Squid starts, it will launch one copy of the channel manager,
and asks the channel manager whether a stale response's freshness
can be extended before refreshing it. Using this stream of
requests, the channel manager is able to subscribe to and keep the
appropriate state nearby.

Channel Manager Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To use the channel manager, you will need:


-  `Squid 2.7 <http://www.squid-cache.org/>`_ or greater
-  `Python 2.5 <http://www.python.org/>`_ or greater, with the
   following extensions:

   -  `Twisted <http://twistedmatrix.com/>`_
   -  `Dateutil <http://labix.org/python-dateutil>`_


To configure Squid for cache channels,


#. Place the channel\_manager.py script in an appropriate location
   (e.g., ``/usr/local/libexec/squid/channel_manager.py``).
#. Place the configuration file in an appropriate location (e.g.,
   ``/usr/local/etc/squid/channel_manager.conf``).
   Edit the configuration file as instructed therein.
#. Add the following line to your squid.conf::
     external_refresh_check children=1 concurrency=99 %CACHE_URI %AGE %RES{Cache-Control} /path/to/this/program /path/to/conf/file``
   (with the paths you chose)
#. Optionally, you can configure logging thorugh squid in
   squid.conf::
     logformat refreshcheck %ts %ru %{Age}<h %{Cache-Control}<h %{Link}<h %Ss %ef
     access_log /var/log/squid/channel_handler.log refreshcheck``
#. Reload Squid configuration;
   ``> squid -k reconfigure``


That's it; Squid will automatically be subscribed to channels that
it sees advertised in Cache-Control response headers.

A sample snippet of squid.conf::

external_refresh_check children=1 concurrency=99 %CACHE_URI %AGE %RES{$(control_header)}
  %RES{Link} /usr/local/libexec/squid/channel_manager.py /usr/local/etc/squid/channel_manager.conf
logformat refreshcheck %ts %ru %{Age}<h %{Cache-Control}<h %{Link}<h %Ss %ef
access_log /var/logs/squid/channel_handler.log refreshcheck

Note that the external_refresh_check line has been wrapped.

Publishing Channels
~~~~~~~~~~~~~~~~~~~

There are a variety of ways to publish an Atom channel. To make an Atom feed
usable for Cache Channels, it's important to:

#. Turn the feed into an Archived feed, as per RFC5005. This means that each
   "page" should have at least a prev-archive link relation, and an archive
   flag when appropriate.
#. Each entry in the feed that intends to mark a URI as stale needs to include
   a ``<cc:stale/>`` flag, where the ``cc`` namespace prefix is mapped to the URI
   ``http://purl.org/syndication/cache-channel``.
#. Stale entries should indicate the URI to mark stale using the ``alternate``
   link relation.

A sample database-backed PHP implementation of a channel publisher is included
in the src directory.

Associating Channels with Responses
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To associate content with a channel, send the following
Cache-Control directives in **all** responses associated with it;

#. ``channel-maxage[=nnn]`` (indicates that its freshness can be
   extended by a channel)
#. ``channel="uri_to_channel"`` (tells caches where the channel is)
#. ``max-age=nn`` (tells caches that it's cacheable in the first
   place)

For example::

  Cache-Control: max-age=60, channel-maxage, channel="http://example.org/chan.atom"

There are a variety of ways to set Cache-Control headers, depending
upon your Web server and publishing environment. See
`the caching tutorial <http://www.mnot.net/cache_docs/>`_ for more
information.

Note that there should be a feed document at the channel URI as
soon as you start advertising it in headers; if it is not present,
subscribing caches will consider it 'down'.


Frequently Asked Questions
--------------------------

How many channels should I use for my content?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Usually, the fewer the better. In most scenarios, it makes sense to
associate all of a Web server's content with one channel URI;
sometimes, it makes sense to associate more than one site's content
with a channel (for example, if the contents of several sites are
tightly interrelated). Having the fewest possible number of
channels increases the efficiency of the system and decreases load
on the server where the channel lives.

You may want to use separate channels if you have an administrative
need to do so; e.g., different people own the content, you want to
use different access controls over the channel contents, or if they
need different parameters (e.g., precision).

How quickly will my stale event be honoured by a cache?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

At most, it will take ``precision`` seconds for an event to
propagate. If there is a failure in the system somewhere, the cache
will notice this before that amount of time, so it's a fairly solid
guarantee.

The one exception to this is when the cache goes to the origin
server to fetch a response (either the first time, or on a
subsequent refresh); then, it will be served fresh for as long as
HTTP allows it to be.

For example, if your channel's ``precision`` is 30 seconds, but the
response has a =Cache-Control: max-age``300`` header associated
with it, the cache won't start looking in the channel for stale
events that apply to this response until five minutes have passed;
then, it will notice such events within 30 seconds.

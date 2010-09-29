<?php
/*
 * PHP Cache Channel Publsher for Stale Events
 * 
 * Copyright (c) 2010 Yahoo! Inc. 
 * 
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 * 
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 * 
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 * DEALINGS IN THE SOFTWARE.
 * 
 * 
 * This is a database-backed publisher for Cache Channels. It requires a
 * MySQL database with the following schema;
 * 
 *  | TEXT uri      | TIMESTAMP event_time  |
 *  | http://foo... | 20080225122312        | 
 *  | http://bar... | 20080225122415        | 
 *  | http://baz... | 20080225131043        | 
 * 
 * Where the primary key is the conjuntion of uri and event_time. event_time
 * should be indexed.
 *
 * Generally, it should be run from a script that sets up configuration
 * variables; see example_channel.php.
 *
 * When called from a Web server, it will serve the channel.
 *
 * When called from the command line, it takes the following options:
 *
 *  --init             Initialise the database
 *  --gc               Garbage-collect the database
 *  --stale="[url]"    Mark [url] stale
 * 
 * NOTE: If you change the $page_frequency, clients will need to re-synch
 * their ENTIRE feeds to assure that state is maintained. This should
 * happen automatically, but may temporarily increase load on your channel's
 * server and database.
 * 
 */

# TODO: allow invalidating multiple URIs

/* functions */

function problem($code, $message) {
	Header("Status: $code");
	Header("Content-Type: text/html");
?>
<html>
	<head>
		<title>Error</title>
	</head>
	<body>
		<h1>Error</h1>
		<p><?php echo $message; ?></p>
	</body>
</html>
<?php
    exit(1);
}

function cli_problem($code, $message) {
        echo "ERROR: " . $message . "\n";
        exit(1); 
}

function get_iso_8601_date($int_date) {
   //$int_date: current date in UNIX timestamp
   $date_mod = date('Y-m-d\TH:i:s', $int_date);
   $pre_timezone = date('O', $int_date);
   $time_zone = substr($pre_timezone, 0, 3).":".substr($pre_timezone, 3, 2);
   $date_mod .= $time_zone;
   return $date_mod;
}


function open_db($conf, $error_func) {
    $db_password = ysecure_get_key($conf['db_keyname']);
    $db_conn = mysql_connect($conf['db_host'], $conf['db_user'], $db_password);
    if (!$db_conn) $error_func(500, "Could not connect to server " . $conf['db_host'] . ": " . mysql_error());
    return $db_conn;
}

# Initialise the Database; called when CLI gives --init
function init_db($conf) {
    $db_conn = open_db($conf, 'cli_problem');
    if (! @mysql_select_db($conf['db_name'])) {
	    $sql = "CREATE DATABASE " . $conf['db_name'];
		if (mysql_query($sql, $db_conn)) {
		    echo "Database " . $conf['db_name'] . " created successfully\n";
		    @mysql_select_db($conf['db_name']);
		} else {
		    cli_problem(500, "Error creating database " . $conf['db_name'] . ": " . mysql_error());
		}
    }
    $sql = "CREATE TABLE IF NOT EXISTS " . $conf['db_table'] . 
        " (uri TEXT(16384) CHARACTER SET ascii NOT NULL, event_time TIMESTAMP NOT NULL, INDEX(event_time))";
#        " (uri TEXT(16384) CHARACTER SET ascii NOT NULL, event_time TIMESTAMP NOT NULL, INDEX(event_time), PRIMARY KEY(uri(900), event_time))";
    mysql_query($sql, $db_conn) or cli_problem(500, "Problem creating table " . $conf['db_name'] . ": " . mysql_error());
    mysql_close($db_conn);
    echo $conf['db_name'] . "." . $conf['db_table'] . " successfully created on " . $conf['db_host'] . "\n";
}

# Garbage collect old invalidations from the db; called when CLI gives --gc
function gc_db($conf) {
    /* Calculate page parameters */
    $now = time();
    // unix timestamp of when the channel's lifetime begins
    $lifetime_start = time() - $conf['channel_lifetime'];

    $db_conn = open_db($conf, 'cli_problem');
    @mysql_select_db($conf['db_name']) or $error_func("Unable to select database " . $conf['db_name'] . ": " . mysql_error());
    $sql = "DELETE from " . $conf['db_table'] . " WHERE event_time < FROM_UNIXTIME(" . $lifetime_start . ")";
    mysql_query($sql, $db_conn) or $error_func(500, "Garbage Collect failed: " . mysql_error());
    mysql_close($db_conn);
}

# Invalidate a URL; called when CLI gives --stale uri (and elsewhere?)
function invalidate_uri($conf, $uri, $error_func) {
    $db_conn = open_db($conf, $error_func);
    @mysql_select_db($conf['db_name']) or $error_func("Unable to select database " . $conf['db_name'] . ": " . mysql_error());        
    $sql = "INSERT into " . $conf['db_table'] . " (uri) values (\"" . mysql_real_escape_string($uri, $db_conn) . "\")";
    mysql_query($sql, $db_conn) or $error_func(500, "Insert failed: " . mysql_error());
    mysql_close($db_conn);
}


# Show the channel
function show_channel($conf) {
    /* Calculate page parameters */
    $now = time();
	// unix timestamp of when the channel's lifetime begins
	$lifetime_start = time() - $conf['channel_lifetime'];
	// corresponding page number, in $page_frequency-sized pages since the epoch
	$lifetime_page = ( $lifetime_start - ($lifetime_start % $conf['page_frequency']) ) / $conf['page_frequency'];
	
	$requested_page = $_GET["page"]; /* page number requested */
	if ($requested_page && ! is_numeric($requested_page) ) {
		problem(400, "Invalid page parameter ($requested_page).");
	}
	if ($requested_page) {
		$page_start_time = max(($requested_page * $conf['page_frequency']), $lifetime_start);
		$page_end_time = $page_start_time + $conf['page_frequency'];
		$prev_page_num = $requested_page - 1;
		$is_archive = true;	
	} else {
		$page_start_time = $now - ( $now % $conf['page_frequency'] );
		$page_end_time = $page_start_time + $conf['page_frequency']; /* add frequency just to be safe. */
		$prev_page_num = ($page_start_time / $conf['page_frequency'] ) - 1;
		$is_archive = false;
	}
	
	# TODO: going back the whole lifetime forces new clients to walk all archives, whether they exist or not. Hmm.
	if ( $prev_page_num > (max(0, $lifetime_page))) {
		/* we include the frequency as a nonce */
		$prev_uri = $_SERVER['PHP_SELF'] . "?page=$prev_page_num&amp;freq=" . $conf['page_frequency'];
	} else {
		$prev_uri = NULL; 
	}
	
	
	/* database access */
	
    $db_conn = open_db($conf, 'problem');
    @mysql_select_db($conf['db_name']) or problem(500, "Unable to select database " . $conf['db_name'] . ": " . mysql_error());        
    $sql = "SELECT uri, UNIX_TIMESTAMP(event_time) as event_timestamp FROM " .
	   $conf['db_table'] . " WHERE event_time between FROM_UNIXTIME(" .
	   $page_start_time . ") and FROM_UNIXTIME(" . $page_end_time .
	   ") order by event_time";
	$result = mysql_query($sql, $db_conn) or problem(500, 'Query failed: ' . mysql_error());
	
	/* output */
	
	if ( $is_archive ) {
		Header("Cache-Control: max-age=" . $conf['channel_lifetime']);
	} else {
		Header("Cache-Control: max-age=" . $conf['channel_frequency']);
	}
	Header("Content-Type: application/atom+xml");
	
	echo '<?xml version="1.0" encoding="utf-8"?>';
?>	
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:cc="http://purl.org/syndication/cache-channel"
      xmlns:fh="http://purl.org/syndication/history/1.0">
 <title><?php echo $conf['channel_title']; ?></title>
 <updated><?php echo get_iso_8601_date($now); ?></updated>
 <author>
  <name><?php echo $conf['channel_author']; ?></name>
 </author>
 <cc:precision><?php echo $conf['channel_precision']; ?></cc:precision>
 <cc:lifetime><?php echo $conf['channel_lifetime']; ?></cc:lifetime>
 <id><?php echo $conf['channel_id']; ?></id>
<?php
	if ( $prev_uri != NULL ) {
		echo " <link rel='prev-archive' href='$prev_uri'/>\n";
	}
	
	if ( $is_archive ) { 
		echo " <cc:archive/>\n";
	}
	
	while ($row = mysql_fetch_array($result, MYSQL_ASSOC)) {
?>
 <entry>
  <title>stale</title>
  <link rel="alternate" href="<?php echo $row['uri']; ?>"/>
  <id><?php echo $row['uri'] . "#" . $row['event_timestamp']; ?></id>
  <updated><?php echo get_iso_8601_date($row['event_timestamp']); ?></updated>
  <summary>stale</summary>
  <cc:stale/>
 </entry>
<?php
	}
	
	echo "</feed>";
	
	mysql_free_result($result);
	mysql_close($db_conn);
}

function arguments($argv_) {
    $_ARG = array();
    foreach ($argv_ as $arg) {
        if (ereg('--[a-zA-Z0-9]*=.*',$arg)) {
            $str = split("=",$arg); $arg = '';
            $key = ereg_replace("--",'',$str[0]);
            for ( $i = 1; $i < count($str); $i++ ) {
                $arg .= $str[$i];
            }
                        $_ARG[$key] = $arg;
        } elseif(ereg('-[a-zA-Z0-9]',$arg)) {
            $arg = ereg_replace("-",'',$arg);
            $_ARG[$arg] = 'true';
        }
    
    }
    return $_ARG;
}

function main($conf) {
	if (php_sapi_name() == 'cli') {
	   $args = arguments($_SERVER['argv']);
	   if (isset($args['init'])) {
           init_db($conf);
	   } elseif (isset($args['gc'])) {
          gc_db($conf);
	   } elseif (isset($args['stale'])) {
	       invalidate_uri($conf, $args['stale'], 'cli_problem');
	   } else {
	       $prog = $_SERVER['argv'][0];
	       echo "USAGE: $prog [option]\n";
	       echo "Options:\n";
	       echo "       --init           Initialise the database\n";
	       echo "       --gc             Garbage-collect the database\n";
	       echo "       --stale=\"[url]\"  Mark [url] stale\n";
	   }	   
	} else {
	   show_channel($conf);
	}
}
?>
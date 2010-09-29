<?php

/*
 * Configuration for channel_publisher.php
 */

/* Database Config -- YOU MUST CHANGE THESE */

$conf = array(
	'db_host' => '127.0.0.1',                   # hostname for MySQL5 database
	'db_name' => 'test',                        # database name
	'db_table' => 'channel_table',              # database table name
	'db_user' => 'bob',                         # database username
	'db_keyname' => 'cache_channel',            # KeyDB keyname for database password
	
	/* Channel Config -- YOU MUST CHANGE THESE */
	
	'channel_title' => "channel",             # title of the channel
	'channel_author' => "me",                 # author's name
	'channel_id' => "http://example.com/",    # Stable URI for channel
	
	/* Channel Setup -- tweaking (defaults are sane) */
	
	'channel_precision' => 60,                # number of seconds of precision in the channel
	'channel_lifetime' => 60 * 60 * 24 * 7,   # number of seconds for channel lifetime
	'page_frequency' => 60 * 15,              # number of seconds between pages
);

/* Adjust as necessary */
require("CacheChannels/db_publish.php");
main($conf);
?>
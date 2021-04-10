# Simple-Proxy-Server

## Introduction
In computer networks, a proxy server is a server that acts as an intermediary for requests from clients seeking resources from other servers. A client connects to the proxy server, requesting some service, such as a file, connection, web page, or other resource available from a different server and the proxy server evaluates the request as a way to simplify and control its complexity. Proxies were invented to add structure and encapsulation to distributed systems.

https://en.wikipedia.org/wiki/Proxy_server

## Usage
In this repository is a simple proxy server and two servers (resource1.js and resource2.js) it can proxy to; all written in node.js.

All three servers offer the same endpoint "GET: /messages". The proxy server allows us to prefix the path to proxy the request to the correct resource.

To start up the servers, run
```sh
$ npm install
$ node proxy.js & node resources/resource1.js & node resources/resource2.js &
Proxy server started up on port 3000
Resource 1 started up on port 3001
Resource 2 started up on port 3002
```
Then in your browser:  
&nbsp;&nbsp;&nbsp;&nbsp; the URL "http://localhost:3000/message" will show the message "This is the Proxy server"  
&nbsp;&nbsp;&nbsp;&nbsp; the URL "http://localhost:3000/resource1/message" will show the message "Received resource 1"  
&nbsp;&nbsp;&nbsp;&nbsp; the URL "http://localhost:3000/resource2/message" will show the message "Received resource 2"  
All three requests go through the same port, 3000, but resource 1 and 2 are running on ports 3001 and 3002 respectively.

To kill all the processes, run
```sh
$  pkill -9 node
```
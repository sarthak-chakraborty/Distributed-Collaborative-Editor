'use strict';
const http = require('http');
const restify = require('restify');
const server = restify.createServer();

const httpProxy = require('http-proxy');
const HttpProxyRules = require('http-proxy-rules');
const proxy = httpProxy.createProxy();

// pre() runs before routing occurs; allowing us to proxy requests to different targets.
server.pre(function (req, res, next) {

 
  return proxy.web(req, res, { "target": "http://127.0.0.1:8001" });
  
  return next();
});

server.listen(8000, function() {
  console.log('Proxy server started up on port 8000');
});

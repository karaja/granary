"""ActivityStreams API handler classes.

Implements the OpenSocial ActivityStreams REST API:
http://opensocial-resources.googlecode.com/svn/spec/2.0.1/Social-API-Server.xml#ActivityStreams-Service
http://opensocial-resources.googlecode.com/svn/spec/2.0.1/Core-Data.xml

Request paths are of the form /user_id/group_id/app_id/activity_id, where
each element is optional. user_id may be @me. group_id may be @all, @friends
(currently identical to @all), or @self. app_id may be @app, but it doesn't
matter, it's currently ignored.

The supported query parameters are startIndex and count, which are handled as
described in OpenSocial (above) and OpenSearch.

Other relevant activity REST APIs:
http://status.net/wiki/Twitter-compatible_API
http://wiki.activitystrea.ms/w/page/25347165/StatusNet%20Mapping
https://developers.google.com/+/api/latest/activities/list

ActivityStreams specs:
http://activitystrea.ms/specs/

Atom format spec:
http://atomenabled.org/developers/syndication/
"""

__author__ = ['Ryan Barrett <granary@ryanb.org>']

import json
import logging
import urllib

from oauth_dropins.webutil import handlers
from oauth_dropins.webutil import util
from webob import exc

from granary import appengine_config
from granary import atom
from granary import microformats2
from granary import source

import webapp2

XML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<response>%s</response>
"""
ITEMS_PER_PAGE = 100

# default values for each part of the API request path except the site, e.g.
# /twitter/@me/@self/@all/...
PATH_DEFAULTS = ((source.ME,), (source.ALL, source.FRIENDS), (source.APP,), ())
MAX_PATH_LEN = len(PATH_DEFAULTS) + 1


class Handler(webapp2.RequestHandler):
  """Base class for ActivityStreams API handlers.

  Attributes:
    source: Source subclass
  """

  handle_exception = handlers.handle_exception

  def get(self):
    """Handles an API GET.

    Request path is of the form /site/user_id/group_id/app_id/activity_id ,
    where each element except site is an optional string object id.
    """
    # parse path
    args = urllib.unquote(self.request.path).strip('/').split('/')
    if not args or len(args) > MAX_PATH_LEN:
      raise exc.HTTPNotFound()

    src_cls = source.sources.get(args.pop(0))
    if not src_cls:
      raise exc.HTTPNotFound()
    src = src_cls(**{key: val for key, val in self.request.params.items()
                     if key.startswith('access_token')})

    # handle default path elements
    args = [None if a in defaults else a
            for a, defaults in zip(args, PATH_DEFAULTS)]
    user_id = args[0] if args else None

    # extract format
    expected_formats = ('json', 'atom', 'xml', 'html', 'json-mf2')
    format = self.request.get('format', 'json')
    if format not in expected_formats:
      raise exc.HTTPBadRequest('Invalid format: %s, expected one of %r' %
                               (format, expected_formats))

    # get activities and build response
    response = src.get_activities_response(*args, **self.get_kwargs())
    activities = response['items']

    # encode and write response
    self.response.headers['Access-Control-Allow-Origin'] = '*'
    if format == 'json':
      self.response.headers['Content-Type'] = 'application/json'
      self.response.out.write(json.dumps(response, indent=2))
    elif format == 'atom':
      actor = src.get_actor(user_id)
      self.response.headers['Content-Type'] = 'text/xml'
      self.response.out.write(atom.activities_to_atom(
          activities, actor, host_url=self.request.host_url + '/',
          request_url=self.request.path_url))
    elif format == 'xml':
      self.response.headers['Content-Type'] = 'text/xml'
      self.response.out.write(XML_TEMPLATE % util.to_xml(response))
    elif format == 'html':
      self.response.headers['Content-Type'] = 'text/html'
      items = [microformats2.object_to_html(a['object'], a.get('context', {}))
               for a in activities]
      self.response.out.write("""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
%s
</body>
</html>
""" % '\n'.join(items))
    elif format == 'json-mf2':
      self.response.headers['Content-Type'] = 'application/json'
      items = [microformats2.object_to_json(a['object'], a.get('context', {}))
               for a in activities]
      self.response.out.write(json.dumps({'items': items}, indent=2))

    if 'plaintext' in self.request.params:
      # override response content type
      self.response.headers['Content-Type'] = 'text/plain'

  def get_kwargs(self):
    """Extracts, normalizes and returns the startIndex, count, and search
    query params.

    Returns:
      dict with 'start_index' and 'count' keys mapped to integers
    """
    start_index = self.get_positive_int('startIndex')
    count = self.get_positive_int('count')

    if count == 0:
      count = ITEMS_PER_PAGE - start_index
    else:
      count = min(count, ITEMS_PER_PAGE)

    kwargs = {'start_index': start_index, 'count': count}

    search_query = self.request.get('q')
    if search_query:
      kwargs['search_query'] = search_query

    return kwargs

  def get_positive_int(self, param):
    try:
      val = self.request.get(param, 0)
      val = int(val)
      assert val >= 0
      return val
    except (ValueError, AssertionError):
      raise exc.HTTPBadRequest('Invalid %s: %s (should be positive int)' %
                               (param, val))


application = webapp2.WSGIApplication([('.*', Handler)],
                                      debug=appengine_config.DEBUG)

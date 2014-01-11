#!/usr/bin/python
"""Instagram source class.
"""

__author__ = ['Ryan Barrett <activitystreams@ryanb.org>']

import datetime
import itertools
import json
import logging
import re
import urllib
import urlparse

import appengine_config
from oauth_dropins.python_instagram.bind import InstagramAPIError
from oauth_dropins.python_instagram.client import InstagramAPI
import source
from webutil import handlers
from webutil import util

# Maps Instagram media type to ActivityStreams objectType.
OBJECT_TYPES = {'image': 'photo', 'video': 'video'}


def handle_exception(self, e, debug):
  """HTTP request exception handler that translates Instagram errors.
  """
  if isinstance(e, InstagramAPIError):
    logging.exception(e)
    self.response.set_status(e.status_code)
    self.response.write(str(e))
  else:
    return handlers.handle_exception(self, e, debug)


class Instagram(source.Source):
  """Implements the ActivityStreams API for Instagram."""

  DOMAIN = 'instagram.com'
  NAME = 'Instagram'
  FRONT_PAGE_TEMPLATE = 'templates/instagram_index.html'

  def __init__(self, access_token=None):
    """Constructor.

    If an OAuth access token is provided, it will be passed on to Instagram.
    This will be necessary for some people and contact details, based on their
    privacy settings.

    Args:
      access_token: string, optional OAuth access token
    """
    self.access_token = access_token
    self.api = InstagramAPI(
      client_id=appengine_config.INSTAGRAM_CLIENT_ID,
      client_secret=appengine_config.INSTAGRAM_CLIENT_SECRET,
      access_token=access_token)

  def get_actor(self, user_id=None):
    """Returns a user as a JSON ActivityStreams actor dict.

    Args:
      user_id: string id or username. Defaults to 'self', ie the current user.

    Raises: InstagramAPIError
    """
    if user_id is None:
      user_id = 'self'
    return self.user_to_actor(self.api.user(user_id))

  def get_activities(self, user_id=None, group_id=None, app_id=None,
                     activity_id=None, start_index=0, count=0,
                     fetch_replies=False, fetch_likes=False,
                     fetch_shares=False, etag=None):
    """Returns a (Python) list of ActivityStreams activities to be JSON-encoded.

    See method docstring in source.py for details. app_id is ignored.

    http://instagram.com/developer/endpoints/users/#get_users_feed
    http://instagram.com/developer/endpoints/users/#get_users_media_recent

    Likes are always included, regardless of the fetch_likes kwarg. They come
    bundled in the 'likes' field of the API Media object:
    http://instagram.com/developer/endpoints/media/#

    Instagram doesn't have a reshare feature, so shares are never included
    since they don't exist. :P

    Raises: InstagramAPIError
    """
    if user_id is None:
      user_id = 'self'
    if group_id is None:
      group_id = source.FRIENDS

    # TODO: paging
    media = []

    try:
      if activity_id:
        media = [self.api.media(activity_id)]
      elif group_id == source.SELF:
        media, _ = self.api.user_recent_media(user_id)
      elif group_id == source.ALL:
        media, _ = self.api.media_popular()
      elif group_id == source.FRIENDS:
        media, _ = self.api.user_media_feed()

    except InstagramAPIError, e:
      if e.error_type == 'APINotFoundError':
        logging.exception(e.error_message)
        media = []
      else:
        raise

    return [self.media_to_activity(m) for m in media]

  def get_comment(self, comment_id, activity_id=None):
    """Returns an ActivityStreams comment object.

    Args:
      comment_id: string comment id
      activity_id: string activity id, required
    """
    media = self.api.media(activity_id)
    for comment in media.comments:
      if comment.id == comment_id:
        return self.comment_to_object(comment, activity_id, media.link)

  def get_share(self, activity_user_id, activity_id, share_id):
    """Not implemented. Returns None. Resharing isn't a feature of Instagram.
    """
    return None

  def media_to_activity(self, media):
    """Converts a media to an activity.

    http://instagram.com/developer/endpoints/media/#get_media

    Args:
      media: python_instagram.models.Media

    Returns:
      an ActivityStreams activity dict, ready to be JSON-encoded
    """
    # Instagram timestamps are evidently all PST.
    # http://stackoverflow.com/questions/10320607
    object = self.media_to_object(media)
    activity = {
      'verb': 'post',
      'published': object['published'],
      'id': object['id'],
      'url': object['url'],
      'actor': object['author'],
      'object': object,
      }

  def media_to_object(self, media):
    """Converts a media to an object.

    Args:
      media: python_instagram.models.Media

    Returns:
      an ActivityStreams object dict, ready to be JSON-encoded
    """
    # TODO: location
    # http://instagram.com/developer/endpoints/locations/
    id = media.id

    object = {
      'id': self.tag_uri(id),
      # TODO: detect videos. (the type field is in the JSON respose but not
      # propagated into the Media object.)
      'objectType': OBJECT_TYPES.get('image', 'photo'),
      'published': media.created_time.isoformat('T'),
      'author': self.user_to_actor(media.user),
      'content': media.caption.text if media.caption else None,
      'url': media.link,
      'attachments': [{
          'objectType': 'image',
          'image': {
            'url': image.url,
            'width': image.width,
            'height': image.height,
            }
          } for image in media.images.values()],
      # comments go in the replies field, according to the "Responses for
      # Activity Streams" extension spec:
      # http://activitystrea.ms/specs/json/replies/1.0/
      'replies': {
        'items': [self.comment_to_object(c, id, media.link)
                  for c in media.comments],
        'totalItems': media.comment_count,
        },
      'tags': [{
          'objectType': 'hashtag',
          'id': self.tag_uri(tag.name),
          'displayName': tag.name,
          # TODO: url
          } for tag in getattr(media, 'tags', [])] +
        [self.user_to_actor(user.user)
         for user in getattr(media, 'users_in_photo', [])] +
        [{
          'id': self.tag_uri('%s_liked_by_%s' % (id, like.id)),
          'url': media.link,
          'objectType': 'activity',
          'verb': 'like',
          'object': {'url': media.link},
          'author': self.user_to_actor(like),
          'content': 'likes this.',
          } for like in getattr(media, 'likes', [])],
      }

    for version in ('standard_resolution', 'low_resolution', 'thumbnail'):
      image = media.images.get(version)
      if image:
        object['image'] = {'url': image.url}
        break

    return util.trim_nulls(object)

  def comment_to_object(self, comment, media_id, media_url):
    """Converts a comment to an object.

    Args:
      comment: python_instagram.models.Comment
      media_id: string
      media_url: string

    Returns:
      an ActivityStreams object dict, ready to be JSON-encoded
    """
    return {
      'objectType': 'comment',
      'id': self.tag_uri(comment.id),
      'inReplyTo': [{'id': self.tag_uri(media_id)}],
      'url': media_url,
      # TODO: add PST time zone
      'published': comment.created_at.isoformat('T'),
      'content': comment.text,
      'author': self.user_to_actor(comment.user),
      }

  def user_to_actor(self, user):
    """Converts a user to an actor.

    Args:
      user: python_instagram.models.Comment

    Returns:
      an ActivityStreams actor dict, ready to be JSON-encoded
    """
    if not user:
      return {}

    id = getattr(user, 'id', None)
    username = getattr(user, 'username', None)
    actor = {
      'id': self.tag_uri(id or username),
      'username': username,
      }
    if not id or not username:
      return actor

    url = getattr(user, 'website', None)
    if not url:
      url = 'http://instagram.com/' + username

    actor.update({
      'objectType': 'person',
      'displayName': user.full_name,
      'image': {'url': user.profile_picture},
      'url': url,
      'description': getattr(user, 'bio', None)
      })

    return util.trim_nulls(actor)

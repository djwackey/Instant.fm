#!/usr/bin/env python

import sys
import os
import re
import json
import io
import base64
import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.database

from datetime import datetime
from optparse import OptionParser
from tornado.options import define, options

define("port", default=8000, help="run on the given port", type=int)
define("mysql_host", default="instant.fm:3306", help="database host")
define("mysql_database", default="instantfm", help="database name")
define("mysql_user", default="instantfm", help="database user")
define("mysql_password", default="CXZrPkkJEgk7lAZMnzbk5hb9g", help="database password")

class Application(tornado.web.Application):
    """Custom application class that keeps a database connection"""
    def __init__(self):
        handlers = [
            (r"/", HomeHandler),
            (r"/upload", UploadHandler),
            (r"/p/([a-zA-Z0-9]*)$", PlaylistHandler),
            (r"/p/([a-zA-Z0-9]*)/json$", PlaylistJSONHandler),
            (r"/p/([a-zA-Z0-9]*)/edit$", PlaylistEditHandler),
            (r"/terms$", TermsHandler),
            (r"/suggest$", ArtistAutocompleteHandler),
            (r".*", ErrorHandler),
        ]
        settings = dict(
            debug=True, # always refresh templates
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            xsrf_cookies=True,
            cookie_secret="SkxQTluCp02hm5k0zbiAJmgg2M3HOS7",
        )
        tornado.web.Application.__init__(self, handlers, **settings)
        
        # TODO: Change this to use UNIX domain sockets?
        self.db = DBConnection(
            host=options.mysql_host, database=options.mysql_database,
            user=options.mysql_user, password=options.mysql_password)
            
class DBConnection(tornado.database.Connection):
    """This is a hacky subclass of Tornado's MySQL connection that allows the number of rows affected by a query to be retreived.
    Why is this functionality not built-in???"""
    def execute_count(self, query, *parameters):
        """Executes the given query, returning the number of rows affected by the query."""
        cursor = self._cursor()
        try:
            return self._execute(cursor, query, parameters)
        finally:
            cursor.close()
            
class BaseHandler(tornado.web.RequestHandler):
    @property
    def db(self):
        """Provides access to the database connection"""
        return self.application.db
        
    def base36_10(self, alpha_id):
        """Converts a base 36 id (0-9a-z) to an integer"""
        playlist_id = 0
        index = 0
        while index < len(alpha_id):
            char = alpha_id[index]
            if str.isdigit(char):
                value = int(char)
            else:
                value = ord(char.lower()) - ord('a') + 10
            playlist_id = playlist_id * 36 + value
            
            index += 1
            
        return playlist_id      
        
    def base10_36(self, playlist_id):
        """Converts an integer id to base 36 (0-9a-z)"""
        alpha_id = ''
        while playlist_id > 0:
            value = playlist_id % 36
            playlist_id = playlist_id // 36
            
            if value < 10:
                char = str(value)
            else:
                char = chr(ord('a') + value - 10)
                
            alpha_id = char + alpha_id
            
        return alpha_id
    
    def makePlaylistJSON(self, playlist_entry):
        """Generate a playlist's JSON representation"""
        user_cookie = self.get_secure_cookie('user_id')
        if user_cookie is None:
            editable = False
        else:    
            editable = (long(user_cookie) == playlist_entry.user_id)
        
        alpha_id = self.base10_36(playlist_entry.playlist_id)
        title = playlist_entry.title
        description = playlist_entry.description
        songs = playlist_entry.songs
        
        # This is a bit of a hack to build up a JSON string that contains pre-converted
        # JSON data (the songs array), which must not be converted again"""
        playlist = ('{"playlist_id": ' + json.dumps(alpha_id) + ', "title": ' + json.dumps(title) +
            ', "description": ' + json.dumps(description) + ', "songs": ' + songs +
            ', "editable": ' + json.dumps(editable) + '}')
        return playlist
    
    def get_error_html(self, status_code, **kwargs):
        """Renders error pages (called internally by Tornado)"""
        if status_code == 404:
            try:
                return open('static/404.html', 'r').read()
            except Exception:
                pass
                
        return super(BaseHandler, self).get_error_html(status_code, **kwargs)
        
    def set_user_cookie(self):
        """Checks if a user_id cookie is set and sets one if not"""
        if not self.get_secure_cookie('user_id'):
            create_date = datetime.utcnow().isoformat(' ')
            new_id = self.db.execute("INSERT INTO users (create_date) VALUES (%s);", create_date)
            self.set_secure_cookie('user_id', str(new_id))
            
class ArtistAutocompleteHandler(BaseHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "http://localhost") # TODO: Remove before production
        prefix = self.get_argument('term');
        artists = self.db.query("SELECT name AS label FROM artist_popularity WHERE listeners > 0 AND (name LIKE %s OR sortname LIKE %s) ORDER BY listeners DESC LIMIT 5", prefix + '%', prefix + '%')
        self.write(json.dumps(artists))
    
class HomeHandler(BaseHandler):
    def get(self):
        self.set_user_cookie()
        self.render("index.html")
        
class TermsHandler(BaseHandler):
    def get(self):
        self.set_user_cookie()
        self.render("terms.html")
        
    
class PlaylistHandler(BaseHandler):
    """Handles requests for a playlist and inserts the correct playlist JavaScript"""
    
    def _get_playlist(self, playlist_id):
        print "Getting playlist ID: " + str(playlist_id)
        return self.db.get("SELECT * FROM playlists WHERE playlist_id = %s;", playlist_id)
    
    def _render_playlist(self, playlist_id):
        """Renders a page with the specified playlist."""
        playlist_entry = self._get_playlist(playlist_id)
        if not playlist_entry:
            print "Couldn't find playlist"
            raise tornado.web.HTTPError(404)
        
        playlist = self.makePlaylistJSON(playlist_entry)
        self.render("playlist.html", playlist=playlist)
    
    def get(self, playlist_alpha_id):
        self.set_user_cookie()
        playlist_id = self.base36_10(playlist_alpha_id)
        self._render_playlist(playlist_id)


class PlaylistJSONHandler(PlaylistHandler):
    """Handles requests to get playlists from the database"""
    
    def _render_playlist(self, playlist_id):
        """Renders the specified playlist's JSON representation"""
        playlist_entry = self._get_playlist(playlist_id)
        
        if not playlist_entry:
            print "Couldn't find playlist"
            self.write(json.dumps({'status': 'Not found'}))
            return
        
        playlist = self.makePlaylistJSON(playlist_entry)
        self.write(playlist)


class PlaylistEditHandler(BaseHandler):
    """Handles updates to playlists in the database"""
    
    # SECURITY WARNING: Do NOT user user-input for col_name, that would be BAD!
    def _update_playlist(self, playlist_id, col_name, col_value, user_id):
        print "Updating playlist ID: " + str(playlist_id)
        return self.db.execute_count("UPDATE playlists SET "+col_name+" = %s WHERE playlist_id = %s AND user_id = %s;", col_value, playlist_id, user_id) == 1
    
    def post(self, playlist_alpha_id):
        user_cookie = self.get_secure_cookie('user_id')
        if not user_cookie:
            self.write(json.dumps({'status': 'No user cookie'}))
            return
            
        user_id = long(user_cookie)
        
        playlist_id = self.base36_10(playlist_alpha_id)
        
        updatableColumns = ['songs', 'title', 'description']
        for col_name in updatableColumns:
            col_value = self.get_argument(col_name, None)
            if col_value is not None:
                # update playlist
                if self._update_playlist(playlist_id, col_name, col_value, user_id):
                    self.write(json.dumps({'status': 'Updated'}))
                else:
                    self.write(json.dumps({'status': 'Playlist not editable'}))
                return
        
        self.write(json.dumps({'status': 'Malformed edit request'}))        
        
        
class UploadHandler(BaseHandler):
    """Handles playlist upload requests"""
    def _parseM3U(self, contents):
        f = io.StringIO(contents.decode('utf-8'), newline=None)
        
        first_line = f.readline()
        if not re.match(r"#EXTM3U", first_line):
            return None
        
        # Attempt to guess if the artist/title are in iTunes order
        itunes_format = False
        while True:
            line = f.readline()
            if len(line) == 0:
                break
                
            if re.match(r"[^#].*([/\\])iTunes\1", line):
                itunes_format = True
                break
        
        f.seek(0)
        
        res_arr = []        
        while True:
            line = f.readline()
            if len(line) == 0:
                break
            
            line = line.rstrip("\n")

            if itunes_format:
                res = re.match(r"#EXTINF:\d*,(.*) - (.*)", line)
                if res:
                    title = res.group(1)
                    artist = res.group(2)
                    res_arr.append({'t': title, 'a': artist})
                    
            else:
                # Slightly different regex to handle dashes in song titles better
                res = re.match(r"#EXTINF:\d*,(.*?) - (.*)", line)
                if res:
                    artist = res.group(1)
                    title = res.group(2)
                    res_arr.append({'t': title, 'a': artist})
                
        return res_arr
        
    def _parse_text(self, contents):
        try:
            decoded = contents.decode('utf-8')
        except Exception:
            decoded = contents.decode('utf-16')
        
        f = io.StringIO(decoded, newline=None)
        
        first_line = f.readline()
        if not re.match(r"Name\tArtist", first_line):
            return None
            
        res_arr = []
        while True:
            line = f.readline()
            if len(line) == 0:
                break
                
            line = line.rstrip("\n")
            
            res = re.match(r"([^\t]*)\t([^\t]*)", line)
            if res:
                title = res.group(1)
                artist = res.group(2)
                res_arr.append({'t': title, 'a': artist})        
    
        return res_arr

    def _parse_pls(self, contents):
        f = io.StringIO(contents.decode('utf-8'), newline=None)
        
        first_line = f.readline()
        if not re.match(r"\[playlist\]", first_line):
            return None
            
        res_arr = []
        while True:
            line = f.readline()
            if len(line) == 0:
                break
                
            line = line.rstrip("\n")
            
            res = re.match(r"Title\d=(.*?) - (.*)", line)
            if res:
                artist = res.group(1)
                title = res.group(2)
                res_arr.append({'t': title, 'a': artist})
                
        return res_arr
        
    def _store_playlist(self, name, description, songs, user_id):
        new_id = self.db.execute("INSERT INTO playlists (title, description, songs, user_id) VALUES (%s,%s,%s,%s);",
            name, description, songs, user_id)

        return self.base10_36(new_id)
                
    def _handle_request(self):
        user_cookie = self.get_secure_cookie('user_id')
        if not user_cookie:
            return {'status': 'No user cookie'}
            
        user_id = long(user_cookie)
        
        # If the file is directly uploaded in the POST body
        # Make a dict of the headers with all lowercase keys
        lower_headers = dict([(key.lower(), value) for (key, value) in self.request.headers.items()])
        if 'up-filename' in lower_headers:
            filename = lower_headers['up-filename']

            if self.get_argument('base64', 'false') == 'true':
                try:                    
                    contents = base64.b64decode(self.request.body)
                except Exception:
                    return {'status': 'Invalid request'}
            else:
                contents = self.request.body
        # If the file is in form/multipart data
        else:
            if 'file' not in self.request.files or len(self.request.files['file']) == 0:
                return {'status': 'No file specified'}
        
            uploaded_file = self.request.files['file'][0]
            filename = uploaded_file['filename']
            contents = uploaded_file['body']

        name, ext = os.path.splitext(filename)
        
        # Parse the file based on the format
        if ext == ".m3u" or ext == ".m3u8":
            parsed = self._parseM3U(contents)
            
        elif ext == ".txt":
            parsed = self._parse_text(contents)
            
        elif ext == ".pls":
            parsed = self._parse_pls(contents)

        else:
            return {'status': 'Unsupported type'}
            
        if parsed is None:
            return {'status': 'Corrupted playlist file'}
            
        playlist_id = self._store_playlist(name, "Imported playlist", json.dumps(parsed), user_id)
        playlist = {
            'status': 'ok',
            'title': name,
            'description': 'Uploaded playlist',
            'songs': parsed,
            'playlist_id': playlist_id,
            'editable': True
        }
        return playlist
    
    def post(self):
        result = self._handle_request()
        
        if self.get_argument('redirect', 'false') == 'true':
            playlist_id = result['playlist_id']
            self.redirect("/p/"+playlist_id)
        else:
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps(result))
        

class ErrorHandler(BaseHandler):
    def prepare(self):
        self.send_error(404)    
    
        
def main():
    # Check for the -d (debug) argument
    optparser = OptionParser()
    optparser.add_option("-d", action="store_false", dest="daemonize", help="don't dameonize (debug mode)", default=True)
    (options, args) = optparser.parse_args()
    
    if options.daemonize:
        try:
            import daemon
            log = open('tornado.log', 'a+')
            context = daemon.DaemonContext(stdout=log, stderr=log, working_directory='.')
            context.open()
        except ImportError:
            print 'python-daemon not installed; not running as daemon'

    http_server = tornado.httpserver.HTTPServer(Application(), xheaders=True)
    http_server.listen(8000)

    # Start the main loop
    tornado.ioloop.IOLoop.instance().start()

if __name__ == "__main__":    
    main()
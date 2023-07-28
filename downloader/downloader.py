import requests, os, re, sys
import socket, json

from .utils import download_file, tag_file, sanitize, get_filename, create_directory
from .context import client_id
from requests.adapters import HTTPAdapter
from halo import Halo

class SoundcloudDownloader(object):
    def __init__(self, args=None):
        self.args = args
        self.url = args.url
        self.dirname = args.dir
        self.API_V2 = "https://api-v2.soundcloud.com"
        self.download_count = 0
        self.session = requests.Session()
        self.session.params.update({'client_id': client_id})
        self.session.mount("http://", adapter = HTTPAdapter(max_retries = 3))
        self.session.mount("https://", adapter = HTTPAdapter(max_retries = 3))

    def can_download_track(self, track):
        is_downloadable = "downloadable" in track and "download_url" in track
        is_streamable = track["streamable"]
        has_stream_url = "stream_url" in track
        for transcoding in track["media"]["transcodings"]:
            if transcoding["format"]["protocol"] == "progressive":
                has_stream_url = True
                break
        return is_downloadable or (is_streamable and has_stream_url)

    def get_track_url(self, track):
        if "downloadable" in track and "download_url" in track:
            return (track["download_url"], track.get("original_format", "mp3"))
        if track["streamable"]:
            if "stream_url" in track:
                return (track["stream_url"], "mp3")
            for transcoding in track["media"]["transcodings"]:
                print("json", str(transcoding))

                if transcoding["format"]["protocol"] == "progressive":
                    r = self.session.get(transcoding["url"])
                    print("json", json.loads(r.text))
                    return (json.loads(r.text)["url"] , "mp3")
        return (None, None)

    def get_track_metadata(self, track):
        artist = "unknown"
        if "publisher_metadata" in track and track["publisher_metadata"]:
            artist = track["publisher_metadata"].get("artist", "")
        elif "user" in track or not artist:
            artist = track["user"]["username"]
        url, fileFormat = self.get_track_url(track)
        return {
            "title": str(track.get("title", track["id"])),
            "artist": artist,
            "year": str(track.get("release_year", "")),
            "genre": str(track.get("genre", "")),
            "format": fileFormat,
            "download_url": url,
            "artwork_url": track["artwork_url"]
        }

    def download_track(self, track):
        metadata = self.get_track_metadata(track)
        filename = get_filename(metadata)
        if metadata['download_url']:
            download_file(self.session, filename, metadata["download_url"])
            try:
                tag_file(filename, metadata)
            except:
                if os.path.isfile("artwork.jpg"): os.remove("artwork.jpg")
            self.download_count += 1
        else:
            print('Cannot download {}'.format(metadata['title']))

    def download_tracks(self, tracks):
        for _, track in filter(lambda x: self.check_track_number(x[0]), enumerate(tracks)):
            return None

    def check_track_number(self, index):
        if self.download_count == self.args.limit:
            return False
        if self.args.include and index + 1 in self.args.include:
            return True
        if self.args.exclude and index + 1 in self.args.exclude:
            return False
        if self.args.range:
            if not self.args.range[0] <= index + 1 <= self.args.range[1]:
                return False
        return True
    
    def get_paginated_tracks(self, url, url_params, num_tracks, filter_func):
        tracks = []
        while len(tracks) < num_tracks:
            json_payload = self.session.get(url, params=url_params).json()
            tracks += json_payload["collection"]
            tracks = list(filter(filter_func, tracks))			
            url = json_payload["next_href"]
        return tracks

    def get_recommended_tracks(self, track, no_of_tracks=10):
        params = {
            "limit": no_of_tracks,
            "offset": 0
        }
        spinner = Halo(text="Fetching tracks similar to {}".format(track['title']))
        spinner.start()
        recommended_tracks_url = "{}/tracks/{}/related".format(self.API_V2, track['id'])
        r = self.session.get(recommended_tracks_url, params=params)
        spinner.stop()
        tracks = r.json()["collection"]
        print("Found {} similar tracks".format(len(tracks)))
        return tracks

    def get_charted_tracks(self, kind, num_tracks=10):
        url_params = {
            "limit": num_tracks,
            "genre": "soundcloud:genres:" + self.args.genre,
            "kind": kind,
        }
        url = "{}/charts".format(self.API_V2)
        spinner = Halo(text="Fetching {} {} tracks".format(num_tracks, kind))
        spinner.start()
        tracks = get_paginated_tracks(
            url, 
            url_params, 
            num_tracks, 
            lambda track: self.can_download_track(track['track'])
        )
        spinner.stop()
        print("Found {} tracks".format(len(tracks)))
        return list(map(lambda x: x["track"], tracks[:num_tracks]))
        

    def get_uploaded_tracks(self, user):
        num_tracks = self.args.limit if self.args.limit else 9999
        params = {
            "limit": num_tracks,
            "offset": 0
        }
        url = "{}/users/{}/tracks".format(self.API_V2, user['id'])
        spinner = Halo(text="Fetching uploads")
        spinner.start()
        tracks = get_paginated_tracks(
            url, 
            url_params, 
            num_tracks, 
            lambda track: self.can_download_track(track)
        )
        spinner.stop()
        print("Found {} uploads".format(len(tracks)))
        return tracks

    def get_liked_tracks(self, user):
        no_of_tracks = self.args.limit if self.args.limit else 9999
        params = {
            "limit": no_of_tracks,
            "offset": 0
        }
        url = "{}/users/{}/likes".format(self.API_V2, user['id'])
        spinner = Halo(text="Fetching likes")
        spinner.start()
        tracks = get_paginated_tracks(
            url, 
            url_params, 
            num_tracks, 
            lambda track: 'playlist' not in track and self.can_download_track(track['track'])
        )        
        spinner.stop()
        print("Found {} likes".format(len(tracks)))
        return list(map(lambda x: x["track"], tracks[:num_tracks]))

    def main(self):
        os.chdir(self.dirname)
        if self.args.top:
            self.get_charted_tracks("top")
            return 
        if self.args.new:
            self.get_charted_tracks("trending")
            return
        spinner = Halo(text="Resolving URL")
        spinner.start()
        params = {
            "url": self.url,
        }
        url = "{}/resolve".format(self.API_V2)
        res = self.session.get(url, params=params)
        if not res.ok:
            print("Could not get a valid response from the SoundCloud API. Please check the API key")
            return
        data = res.json()
        spinner.stop()
        tracks = []
        if isinstance(data, dict):
            if data['kind'] == "user":
                print("User profile found")
                create_directory(data['username'])
                print("Saving in: " + os.getcwd())
                if self.args.all or self.args.likes:
                    tracks = self.get_liked_tracks(data)
                if not self.args.likes:
                    tracks = self.get_uploaded_tracks(data)
            elif data['kind'] == "track":
                    print("Single track found")
                    print("Saving in: " + os.getcwd())
                    tracks = data
                    self.download_track(tracks)

                    if self.args.similar:
                        tracks = self.get_recommended_tracks(data)
            elif data['kind'] == "playlist":
                print("Single playlist found.")
                create_directory(data['user']['username'])
                tracks = playlist['tracks']
        elif isinstance(data, list):
            if data[0]['kind'] == "playlist":
                print("%d playlists found" % (len(data)))
                for playlist in data:
                    tracks += playlist['tracks']
            elif data[0]['kind'] == "track":
                tracks = data
        self.download_tracks(tracks)

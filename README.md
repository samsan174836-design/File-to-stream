[![Deploy to Heroku](https://img.shields.io/badge/Deploy%20to-Heroku-purple?style=for-the-badge&logo=heroku)](https://www.heroku.com/deploy?template=https://github.com/samsan174836-design/File-to-stream)  

## YouTube Study & Knowledge

Set the server-side `YOUTUBE_API_KEY` environment variable to a YouTube Data API v3 key with YouTube Data API v3 enabled. The key is used only by `/api/youtube/search` and is never sent to the browser. Without it, the Study & Knowledge search reports that it is not configured.

Searches use the official Data API for embeddable videos and playlists with strict SafeSearch. Live broadcasts and videos three minutes or shorter are excluded as Shorts (the API does not provide a dedicated Shorts flag); results are not restricted to videos at least four minutes long. Save videos and playlists from search to the browser's Later Lecture section. Each YouTube result opens in a focused player at `/yt/{video_id}` or `/yt/playlist/{playlist_id}`. After 25 minutes of active playback, the player pauses for a five-minute break and then resumes. The player also links to the equivalent `yout-ube.com` URL; videos with embedding disabled stay on this site and show an explanatory message.

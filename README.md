[![Deploy to Heroku](https://img.shields.io/badge/Deploy%20to-Heroku-purple?style=for-the-badge&logo=heroku)](https://www.heroku.com/deploy?template=https://github.com/samsan174836-design/File-to-stream)  

## Private Lecture Library

After opening your private library with `/website` in the Telegram bot, **Today Lecture** shows videos sent to the bot within the last 24 hours. Videos leave that section automatically after 24 hours and remain available in **All Lecture**. Both sections show only videos sent from the Telegram account used to log in; Telegram thumbnails are displayed when available.

## YouTube Study & Knowledge

Set the server-side `YOUTUBE_API_KEY` environment variable to a YouTube Data API v3 key with YouTube Data API v3 enabled. The key is used only by `/api/youtube/search` and is never sent to the browser. Without it, the Study & Knowledge search reports that it is not configured.

Searches use the official Data API for videos and playlists with strict SafeSearch; videos are not limited to ones that allow embedding. Live broadcasts and videos three minutes or shorter are excluded as Shorts (the API does not provide a dedicated Shorts flag). Save videos and playlists from search to the browser's Later Lecture section. YouTube results open at `/yt/{video_id}` or `/yt/playlist/{playlist_id}`; items with embedding disabled show an explanatory message and can be opened via the external link. The YouTube player and `/show/{id}` media viewer include a four-cycle Pomodoro timer: 25 minutes of focus followed by a five-minute break.

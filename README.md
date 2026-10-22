[![Deploy to Heroku](https://img.shields.io/badge/Deploy%20to-Heroku-purple?style=for-the-badge&logo=heroku)](https://www.heroku.com/deploy?template=https://github.com/samsan174836-design/File-to-stream)  

## Private Lecture Library

After opening your private library with `/website` in the Telegram bot, **Today Lecture** shows videos sent to the bot within the last 24 hours. Videos leave that section automatically after 24 hours and remain available in **All Lecture**. Both sections show only videos sent from the Telegram account used to log in; Telegram thumbnails are displayed when available.

## YouTube

Set the server-side `YOUTUBE_API_KEY` environment variable to a YouTube Data API v3 key with YouTube Data API v3 enabled. The key is used only by `/api/youtube/search` and is never sent to the browser. Without it, YouTube search reports that it is not configured. Search suggestions are fetched as the user types.

Searches use the YouTube Data API for videos and playlists with strict SafeSearch; videos are not limited to ones that allow embedding. Video results show view counts and relative upload times. Results are paginated with **Load more**. Live broadcasts and videos three minutes or shorter are excluded as a Shorts heuristic (the API does not provide a dedicated Shorts flag). Save videos and playlists from search to the browser's Later Lecture section. YouTube videos open on an in-site player page with YouTube's native controls and page-level keyboard shortcuts; the embedded player is skipped by Tab navigation. Videos that disallow embedding can still be opened on `www.yout-ube.com`; playlists open there directly. Legacy `/yt/{video_id}` and `/yt/playlist/{playlist_id}` links continue to redirect there. The `/show/{id}` media viewer starts playback after buffering about two seconds and saves its resume position server-side for the same browser.

In either in-site player, press **S** to cycle playback speeds; the file player's speed button does the same. Library video cards use Telegram thumbnails when available and generate a thumbnail from the video when one is missing. Thumbnails are retried when fetching/generating fails, saved in MongoDB per video, and also cached in this browser's IndexedDB so they remain available across visits.

## Render deployment

Set `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`, `STORAGE_CHANNEL`, and `DATABASE_URL` in the Render service's environment. `BASE_URL` is optional on Render: when it is not set, the app uses Render's `RENDER_EXTERNAL_URL` to build Telegram stream and website links. Set `BASE_URL` explicitly if you use a custom domain. The bot must be an administrator in the storage channel, and the MongoDB database must be reachable by the Render service.

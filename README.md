[![Deploy to Heroku](https://img.shields.io/badge/Deploy%20to-Heroku-purple?style=for-the-badge&logo=heroku)](https://www.heroku.com/deploy?template=https://github.com/samsan174836-design/File-to-stream)  

## YouTube Study & Knowledge

Set the server-side `YOUTUBE_API_KEY` environment variable to a YouTube Data API v3 key with YouTube Data API v3 enabled. The key is used only by `/api/youtube/search` and is never sent to the browser. Without it, the Study & Knowledge search reports that it is not configured.

Searches use the official Data API with `type=video`, embeddable-video filtering, and strict SafeSearch. Live broadcasts and videos shorter than four minutes are excluded (the API does not provide a dedicated Shorts flag). Playback uses the official YouTube IFrame Player API; videos with embedding disabled stay on this site and show an explanatory message.

# The Fav Club

A small, local Python server for the curtain-reveal website. The server uses only the Python standard library.

## Run

From this folder, start the site with:

```powershell
python app.py
```

Then open <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`.

## Manage pictures

Open <http://127.0.0.1:8000/admin> and sign in with username `nrobins` and the password you provided. Upload a JPG, PNG, WEBP, or GIF up to 8 MB, then select its thumbnail to put it behind the curtain. Your selection is saved across server restarts.

Pictures in `pictures/` are shown as thumbnails in the admin page. The admin page is intended for local use only; the server binds to `127.0.0.1` and should not be exposed to the public internet with the built-in credentials.

## Pass it on

After opening the curtain, choose **Pass it on**, upload a JPG, PNG, WEBP, or GIF up to 8 MB, then copy the generated reveal link. Each link points to its own uploaded picture and does not change the featured picture on the main page. Anyone with a link can view its photo.

The server currently runs on your own computer at `127.0.0.1`, so generated links work there only. To share with someone on the internet, deploy the site to a publicly reachable host and use that hosted address; do not expose this local server or its built-in admin password directly to the internet.

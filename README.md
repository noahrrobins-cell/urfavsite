# The Fav Club

A small, local Python server for the curtain-reveal website. The server uses only the Python standard library.

## Run

From this folder, start the site with:

```powershell
py app.py
```

Then open <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`.

## Choose the revealed picture

Put a `.jpg`, `.jpeg`, `.png`, `.webp`, or `.gif` image in `pictures/`, then set `FEATURED_PICTURE` near the top of `app.py` to that filename. For example:

```python
FEATURED_PICTURE = "portrait-02.jpg"
```

Restart the server and refresh the page to see the selected picture. Four sample portraits are included in `pictures/`; you can replace them with pictures you have permission to use.

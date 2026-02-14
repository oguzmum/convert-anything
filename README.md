Goal of this project: convert images and other file types in between common formats
I don't want to use online services anymore as we never know what happens in the background really
I'll host this app on my homeserver, so it will be a webapplication

---

Idea: 
- User uploads file
  - either chooses filetype or is automatically detected
- choose type the file will be converted to
- click on convert
- live preview
- download button to download the file

- if the file has mutliple pages display each page and let the user choose which pages will be converted

- I will also add conversion between different number systems, currencies etc just to play around and learn :D

---

# Requirements
Created the requirements file with: 
```
pip freeze > requirements.txt
```

---

# Tech Stack

**Backend**
* Python
* FastAPI

**Frontend**
* HTMX
* Jinja2 Templates (kein React, kein Build-Step, einfach HTML + ein bisschen HTMX)

**Conversion Engine (im Docker-Container)**
* ImageMagick
  → für JPG, PNG, HEIC, PDF (teilweise)
* libheif
  → damit ImageMagick HEIC/HEIF lesen kann
* poppler-utils (später)
  → für sauberes PDF → PNG/JPG (`pdftoppm`)
* optional später: Ghostscript
  → für PDF-Erzeugung/Optimierung
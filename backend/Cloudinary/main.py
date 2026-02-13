rom fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def read_root():
    return """
    <html>
        <head>
            <title>Graduation Project Backend</title>
            <style>
                body { font-family: Arial, sans-serif; background-color: #f4f4f9; text-align: center; padding: 50px; }
                h1 { color: #4a90e2; }
                p { font-size: 18px; }
                .footer { margin-top: 50px; font-size: 14px; color: #888; }
            </style>
        </head>
        <body>
            <h1>Graduation Project Backend</h1>
            <p>Author: Nada Harby</p>
            <p>Project is running successfully ✅</p>
            <div class="footer">FastAPI & Cloudinary Backend</div>
        </body>
    </html>
    """

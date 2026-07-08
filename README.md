# Smart Attendance System Using Face Recognition and Geofencing

This project is a secure attendance automation system built with Python, Flask, OpenCV, and face_recognition. It verifies student identity, liveness, location geofence, and timestamps before recording attendance.

## Features
- Student login and dashboard
- Admin panel for student registration and reports
- Face registration with encoding storage
- Liveness detection using blink movement
- Geofence attendance validation
- SQLite backend for attendance logs

## Installation
1. Create a Python virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Run the app:
   ```powershell
   python app.py
   ```
4. Open `http://127.0.0.1:5000` in your browser.

## Default Credentials
- Admin user: `admin`
- Password: `admin123`

## Project Structure
- `app.py` - Flask backend and attendance logic
- `templates/` - HTML interface pages
- `static/css/` - CSS styles
- `static/js/` - JavaScript for camera capture and geolocation

## Notes
- This demo uses a fixed geofence center. Update `GEOFENCE_CENTER` in `app.py` with your campus coordinates.
- Student face registration is performed by uploading a clear front-facing photo.

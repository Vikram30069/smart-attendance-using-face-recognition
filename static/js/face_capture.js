const videoElement = document.getElementById("video");
const canvasElement = document.getElementById("canvas");
const markButton = document.getElementById("markButton");
const attendanceResult = document.getElementById("attendanceResult");

async function startCamera() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        videoElement.srcObject = stream;
    } catch (error) {
        attendanceResult.textContent = "Camera access is required for attendance capture.";
        attendanceResult.style.color = "#b22";
    }
}

function captureFrame() {
    const width = videoElement.videoWidth;
    const height = videoElement.videoHeight;
    canvasElement.width = width;
    canvasElement.height = height;
    const context = canvasElement.getContext("2d");
    context.drawImage(videoElement, 0, 0, width, height);
    return canvasElement.toDataURL("image/jpeg", 0.85);
}

async function getLocation() {
    return new Promise((resolve, reject) => {
        if (!navigator.geolocation) {
            reject(new Error("Geolocation is not supported by this browser."));
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (position) => {
                resolve({
                    latitude: position.coords.latitude,
                    longitude: position.coords.longitude,
                });
            },
            (error) => reject(error),
            { enableHighAccuracy: true, timeout: 15000 },
        );
    });
}

async function markAttendance() {
    attendanceResult.textContent = "Capturing frames and verifying attendance...";
    attendanceResult.style.color = "#333";

    if (!videoElement.srcObject) {
        attendanceResult.textContent = "Camera not initialized. Please allow access.";
        attendanceResult.style.color = "#b22";
        return;
    }

    const frame1 = captureFrame();
    attendanceResult.textContent = "Hold still for 1 second while the system checks liveness...";
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const frame2 = captureFrame();

    let location;
    try {
        location = await getLocation();
    } catch (error) {
        attendanceResult.textContent = "Location access failed: " + error.message;
        attendanceResult.style.color = "#b22";
        return;
    }

    const formData = new FormData();
    formData.append("image1", frame1);
    formData.append("image2", frame2);
    formData.append("latitude", location.latitude);
    formData.append("longitude", location.longitude);

    const response = await fetch("/mark_attendance", {
        method: "POST",
        body: formData,
    });
    const result = await response.json();
    attendanceResult.textContent = result.message;
    attendanceResult.style.color = result.success ? "#1d7a1d" : "#b22";
    if (result.success) {
        markButton.disabled = true;
        markButton.textContent = "Attendance Marked";
    }
}

markButton.addEventListener("click", markAttendance);
startCamera();

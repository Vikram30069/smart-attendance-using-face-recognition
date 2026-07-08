import datetime
import json
import secrets
import string
import base64
import csv
import numpy as np
import face_recognition
import cv2
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.db import IntegrityError, models
from django.db.models import Q

from .models import Section, User, ClassSchedule, AttendanceSession, Attendance, Notification
from .helpers import (
    hash_password,
    verify_password,
    is_strong_password,
    serialize_encoding,
    deserialize_encoding,
    is_duplicate_face,
    calculate_distance,
    is_within_geofence,
    image_from_base64,
    get_face_encoding_from_image,
    get_face_encoding_from_images,
    is_liveness_valid,
    is_liveness_valid_v2,
    GEOFENCE_CENTER,
)

# ================= HELPER FUNCTIONS =================


def get_user_by_username(username):
    return User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username)).first()


def get_user_by_id(user_id):
    return User.objects.filter(id=user_id).first()


def attendance_summary(user_id):
    total = Attendance.objects.filter(user_id=user_id).count()
    present = Attendance.objects.filter(user_id=user_id, status="Present").count()
    percentage = round((present / total) * 100, 1) if total else 0.0
    return total, present, percentage


def get_present_students_for_session(session_id):
    records = Attendance.objects.filter(session_id=session_id, status="Present").select_related("user").order_by("time")
    students = []
    for r in records:
        students.append({
            "id": r.user.id,
            "name": r.user.name,
            "roll_number": r.user.roll_number,
            "time": r.time,
        })
    return students


def get_attendance_logs(user_id=None):
    if user_id:
        return Attendance.objects.filter(user_id=user_id).select_related("user").order_by("-id")
    return Attendance.objects.select_related("user").order_by("-id")


def get_active_attendance_session(teacher_id=None):
    now = datetime.datetime.utcnow().isoformat()
    if teacher_id:
        return (
            AttendanceSession.objects.filter(teacher_id=teacher_id, is_active=1, expires_at__gt=now)
            .select_related("class_schedule", "class_schedule_old", "section")
            .order_by("-started_at")
            .first()
        )
    return (
        AttendanceSession.objects.filter(is_active=1, expires_at__gt=now)
        .select_related("class_schedule", "class_schedule_old", "section")
        .order_by("-started_at")
        .first()
    )


def get_teacher_schedule(teacher_id=None):
    if teacher_id:
        schedules = list(ClassSchedule.objects.filter(teacher_id=teacher_id).select_related("section"))
    else:
        schedules = list(ClassSchedule.objects.select_related("section"))

    day_order = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6, "SUN": 7}

    def sort_key(s):
        d = day_order.get(s.day.upper()[:3], 8)
        return (d, s.start_time)

    schedules.sort(key=sort_key)
    return schedules


def get_student_section(student_id):
    user = User.objects.filter(id=student_id).select_related("section").first()
    return user.section if user else None


def get_section_schedule(section_id):
    schedules = list(ClassSchedule.objects.filter(section_id=section_id).exclude(subject="Leisure").select_related("teacher"))
    day_order = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6, "SUN": 7}

    def sort_key(s):
        d = day_order.get(s.day.upper()[:3], 8)
        return (d, s.start_time)

    for s in schedules:
        if s.teacher and not s.teacher_name:
            s.teacher_name = s.teacher.name
    schedules.sort(key=sort_key)
    return schedules


def get_active_session_for_section(section_id):
    now = datetime.datetime.utcnow().isoformat()
    return (
        AttendanceSession.objects.filter(
            Q(section_id=section_id)
            | Q(class_schedule__section_id=section_id)
            | Q(class_schedule_old__section_id=section_id),
            is_active=1,
            expires_at__gt=now,
        )
        .select_related("class_schedule", "class_schedule_old", "teacher")
        .order_by("-started_at")
        .first()
    )


def get_teacher_sections(teacher_id):
    schedules = ClassSchedule.objects.filter(teacher_id=teacher_id).select_related("section")
    sections = []
    seen = set()
    for s in schedules:
        if s.section and s.section.id not in seen:
            seen.add(s.section.id)
            sections.append(s.section)
    return sections


def get_teacher_today_schedule(teacher_id):
    today_code = datetime.datetime.now().strftime("%a").upper()[:3]
    return (
        ClassSchedule.objects.filter(teacher_id=teacher_id, day__istartswith=today_code)
        .select_related("section")
        .order_by("start_time")
    )


def can_teacher_start_session(teacher_id, class_schedule_id):
    schedule = ClassSchedule.objects.filter(id=class_schedule_id, teacher_id=teacher_id).first()
    if not schedule:
        return False, "This class is not assigned to you"

    if schedule.subject == "Leisure" or schedule.class_name == "Leisure":
        return False, "Cannot start attendance session for a leisure period"

    today_code = datetime.datetime.now().strftime("%a").upper()[:3]
    schedule_day_code = (schedule.day or "").upper()[:3]

    if schedule_day_code != today_code:
        return False, "This class is not scheduled for today"

    now = datetime.datetime.now()
    try:
        start_time = datetime.datetime.strptime(schedule.start_time, "%H:%M")
        end_time = datetime.datetime.strptime(schedule.end_time, "%H:%M")
    except ValueError:
        return False, "Invalid time format in schedule"

    start_time = start_time.replace(year=now.year, month=now.month, day=now.day)
    end_time = end_time.replace(year=now.year, month=now.month, day=now.day)

    time_buffer = datetime.timedelta(minutes=5)
    if not (start_time - time_buffer <= now <= end_time + time_buffer):
        return False, f"Can only start session between {schedule.start_time} and {schedule.end_time}"

    return True, "Ready to start session"


def create_notification(user_id, title, message, notification_type="info", session_id=None, data=None):
    return Notification.objects.create(
        user_id=user_id,
        session_id=session_id,
        type=notification_type,
        title=title,
        message=message,
        data=json.dumps(data) if data else None,
        is_read=0,
        created_at=datetime.datetime.utcnow().isoformat(),
    )


def get_unread_notifications(user_id, limit=10):
    return Notification.objects.filter(user_id=user_id, is_read=0).order_by("-created_at")[:limit]


def mark_notification_read(notification_id):
    Notification.objects.filter(id=notification_id).update(is_read=1)


def notify_section_attendance_started(section_id, session_id, subject, teacher_name):
    students = User.objects.filter(section_id=section_id, role="student")
    sess = AttendanceSession.objects.filter(id=session_id).first()

    teacher_lat = sess.latitude if sess else None
    teacher_lon = sess.longitude if sess else None

    if teacher_lat is None or teacher_lon is None:
        cs = (
            ClassSchedule.objects.filter(id=sess.class_schedule_id).first()
            if sess and sess.class_schedule_id
            else None
        )
        if cs:
            teacher_lat = cs.latitude
            teacher_lon = cs.longitude

    for student in students:
        data = {
            "session_id": session_id,
            "section_id": section_id,
            "subject": subject,
            "teacher_name": teacher_name,
            "teacher_latitude": teacher_lat,
            "teacher_longitude": teacher_lon,
        }
        create_notification(
            user_id=student.id,
            title=f"Attendance: {subject}",
            message=f"Your teacher {teacher_name} has started the attendance session. You can now mark your attendance.",
            notification_type="attendance_started",
            session_id=session_id,
            data=data,
        )


# ================= VIEW HANDLERS =================


def home(request):
    if request.session.get("user_id"):
        return redirect("dashboard")
    return redirect("login")


def login_view(request):
    if request.session.get("user_id"):
        return redirect("dashboard")

    if request.method == "POST":
        identifier = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = get_user_by_username(identifier)
        if user:
            if user.role.strip().lower() == "teacher":
                messages.error(request, "Teachers must use the Teacher Portal to log in.", extra_tags="danger")
                return render(request, "login.html")
            elif verify_password(password, user.password_hash):
                request.session["user_id"] = user.id
                request.session["role"] = user.role
                return redirect("dashboard")
            else:
                messages.error(request, "Invalid username/email or password", extra_tags="danger")
        else:
            messages.error(request, "Invalid username/email or password", extra_tags="danger")
    return render(request, "login.html")


def teacher_login_view(request):
    if request.session.get("user_id"):
        return redirect("dashboard")

    if request.method == "POST":
        identifier = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = get_user_by_username(identifier)
        if user and user.role == "teacher" and verify_password(password, user.password_hash):
            request.session["user_id"] = user.id
            request.session["role"] = user.role
            return redirect("dashboard")
        messages.error(request, "Invalid teacher username/email or password", extra_tags="danger")
    return render(request, "teacher_login.html")


def forgot_password_view(request):
    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()
        password = request.POST.get("password", "").strip()
        confirm_password = request.POST.get("confirm_password", "").strip()
        if not identifier or not password or not confirm_password:
            messages.error(request, "Please enter your username or email and a new password.", extra_tags="danger")
            return redirect("forgot_password")
        if password != confirm_password:
            messages.error(request, "Passwords do not match. Please try again.", extra_tags="danger")
            return redirect("forgot_password")
        if not is_strong_password(password):
            messages.error(
                request,
                "Choose a stronger password: at least 8 characters with uppercase, lowercase, number, and symbol.",
                extra_tags="danger",
            )
            return redirect("forgot_password")
        user = get_user_by_username(identifier)
        if not user:
            messages.error(request, "No account found for that username or email.", extra_tags="danger")
            return redirect("forgot_password")

        user.password_hash = hash_password(password)
        user.save()
        messages.success(request, "Password updated successfully. You may now login.", extra_tags="success")
        if user.role.strip().lower() == "teacher":
            return redirect("teacher_login")
        return redirect("login")
    return render(request, "forgot_password.html")


def signup_view(request):
    if request.session.get("user_id"):
        return redirect("dashboard")

    sections = Section.objects.order_by("department", "section_name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        roll_number = request.POST.get("roll_number", "").strip()
        department = request.POST.get("department", "").strip()
        email = request.POST.get("email", "").strip()
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        section_id = request.POST.get("section_id")

        valid_files = [f for f in request.FILES.getlist("face_images") if f.name]
        if not valid_files and request.FILES.get("face_image"):
            valid_files = [request.FILES.get("face_image")]

        if not all([name, roll_number, department, email, username, password, section_id, valid_files]):
            messages.error(
                request,
                "Please fill in all fields, choose a section, and upload at least one face image.",
                extra_tags="danger",
            )
            return render(
                request,
                "signup.html",
                {
                    "sections": sections,
                    "name": name,
                    "email": email,
                    "roll_number": roll_number,
                    "department": department,
                    "section_id": section_id,
                    "username": username,
                },
            )
        if not is_strong_password(password):
            messages.error(
                request,
                "Choose a stronger password: at least 8 characters with uppercase, lowercase, number, and symbol.",
                extra_tags="danger",
            )
            return render(
                request,
                "signup.html",
                {
                    "sections": sections,
                    "name": name,
                    "email": email,
                    "roll_number": roll_number,
                    "department": department,
                    "section_id": section_id,
                    "username": username,
                },
            )

        try:
            section_id = int(section_id)
        except (TypeError, ValueError):
            section_id = None

        section = Section.objects.filter(id=section_id).first()
        if not section:
            messages.error(request, "Please select a valid section.", extra_tags="danger")
            return render(
                request,
                "signup.html",
                {
                    "sections": sections,
                    "name": name,
                    "email": email,
                    "roll_number": roll_number,
                    "department": department,
                    "section_id": section_id,
                    "username": username,
                },
            )

        existing_user = get_user_by_username(username)
        if existing_user:
            if existing_user.username.lower() == username.lower():
                messages.error(request, "This username is already taken. Choose a different username.", extra_tags="danger")
            elif existing_user.email and existing_user.email.lower() == email.lower():
                messages.error(request, "This email is already registered. Use a different email.", extra_tags="danger")
            else:
                messages.error(
                    request,
                    "A user with this username or email already exists. Please use different credentials.",
                    extra_tags="danger",
                )
            return render(
                request,
                "signup.html",
                {
                    "sections": sections,
                    "name": name,
                    "email": email,
                    "roll_number": roll_number,
                    "department": department,
                    "section_id": section_id,
                    "username": username,
                },
            )

        uploaded_images = []
        for image_file in valid_files:
            image_data = image_file.read()
            uploaded_images.append(image_from_base64(base64.b64encode(image_data).decode("utf-8")))

        encoding = get_face_encoding_from_images(uploaded_images)
        if encoding is None:
            messages.error(
                request, "No face detected in the uploaded images. Use clear front-facing photos.", extra_tags="danger"
            )
            return render(
                request,
                "signup.html",
                {
                    "sections": sections,
                    "name": name,
                    "email": email,
                    "roll_number": roll_number,
                    "department": department,
                    "section_id": section_id,
                    "username": username,
                },
            )

        if is_duplicate_face(encoding):
            messages.error(
                request,
                "A similar face is already registered. Use a different profile or ask the administrator to resolve duplicate face data.",
                extra_tags="danger",
            )
            return render(
                request,
                "signup.html",
                {
                    "sections": sections,
                    "name": name,
                    "email": email,
                    "roll_number": roll_number,
                    "department": department,
                    "section_id": section_id,
                    "username": username,
                },
            )

        try:
            User.objects.create(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role="student",
                name=name,
                roll_number=roll_number,
                department=department,
                section_id=section_id,
                year=section.year,
                face_encoding=serialize_encoding(encoding),
                created_at=datetime.datetime.utcnow().isoformat(),
            )
            messages.success(request, "Signup successful. You can now login.", extra_tags="success")
            return redirect("login")
        except IntegrityError:
            messages.error(request, "Username or email already exists. Please choose a different one.", extra_tags="danger")
            return render(
                request,
                "signup.html",
                {
                    "sections": sections,
                    "name": name,
                    "email": email,
                    "roll_number": roll_number,
                    "department": department,
                    "section_id": section_id,
                    "username": username,
                },
            )

    return render(request, "signup.html", {"sections": sections})


def logout_view(request):
    request.session.flush()
    return redirect("login")


def dashboard(request):
    user_id = request.session.get("user_id")
    if not user_id:
        return redirect("login")

    user = get_user_by_id(user_id)
    if not user:
        request.session.flush()
        return redirect("login")

    if user.role == "admin":
        students = User.objects.filter(role="student").select_related("section").order_by("name")
        teachers = User.objects.filter(role="teacher").order_by("name")
        attendance_count = Attendance.objects.count()
        today = datetime.date.today().isoformat()
        today_count = Attendance.objects.filter(date=today).count()
        schedules = list(ClassSchedule.objects.select_related("teacher", "section"))
        day_order = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6, "SUN": 7}
        schedules.sort(key=lambda s: (s.teacher.name.lower() if s.teacher and s.teacher.name else "", day_order.get(s.day.upper()[:3], 8), s.start_time))
        sections = Section.objects.order_by("department", "section_name")
        return render(
            request,
            "admin_dashboard.html",
            {
                "user": user,
                "students": students,
                "teachers": teachers,
                "attendance_count": attendance_count,
                "today_count": today_count,
                "schedules": schedules,
                "sections": sections,
            },
        )

    if user.role == "teacher":
        sections = get_teacher_sections(user.id)
        active_session = get_active_attendance_session(user.id)
        today_schedule = get_teacher_today_schedule(user.id)
        schedule = get_teacher_schedule(user.id)

        # Group weekly schedule by day
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        weekly_schedule = {day: [] for day in day_names}
        for item in schedule:
            day_found = False
            item_day = item.day.strip().title()
            for d in day_names:
                if item_day == d or item_day[:3].upper() == d[:3].upper():
                    weekly_schedule[d].append(item)
                    day_found = True
                    break
            if not day_found:
                if item_day not in weekly_schedule:
                    weekly_schedule[item_day] = []
                weekly_schedule[item_day].append(item)
        weekly_schedule_list = [(day, list_s) for day, list_s in weekly_schedule.items() if list_s]
        
        # Build weekly grid schedule for table format view
        days_short = ["MON", "TUE", "WED", "THU", "FRI", "SAT"]
        days_full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        slots_info = [
            ("09:30", "10:30"),
            ("10:30", "11:30"),
            ("11:30", "12:30"),
            ("13:10", "14:10"),
            ("14:10", "15:10"),
            ("15:10", "16:10"),
        ]
        
        table_grid = []
        for d_idx, day_short in enumerate(days_short):
            day_full = days_full[d_idx]
            slots_data = []
            
            is_5_period_day = day_short in ["MON", "WED", "FRI"]
            
            for p_idx, (start, end) in enumerate(slots_info):
                if p_idx == 5 and is_5_period_day:
                    slots_data.append({
                        "type": "na",
                        "label": "N/A",
                        "color": "#cbd5e1"
                    })
                    continue
                
                match = None
                for item in schedule:
                    item_day_upper = item.day.strip().upper()[:3]
                    if item_day_upper == day_short and item.start_time == start:
                        match = item
                        break
                
                if match:
                    if match.subject == "Leisure":
                        slots_data.append({
                            "type": "leisure",
                            "label": "Leisure",
                            "color": "#f8fafc"
                        })
                    else:
                        slots_data.append({
                            "type": "class",
                            "label": match.subject,
                            "sub_label": f"{match.section_name} ({match.room})",
                            "color": "#e0e7ff",
                            "item": match
                        })
                else:
                    slots_data.append({
                        "type": "leisure",
                        "label": "Leisure",
                        "color": "#f8fafc"
                    })
            
            table_grid.append({
                "day": day_full,
                "slots": slots_data
            })

        active_session_students = []
        active_session_present_count = 0
        active_session_total_students = 0

        if active_session:
            active_session_students = get_present_students_for_session(active_session.id)
            active_session_present_count = len(active_session_students)
            active_session_total_students = User.objects.filter(role="student", section_id=active_session.section_id).count()

        selected_section_id = request.GET.get("section_id")
        try:
            selected_section_id = int(selected_section_id) if selected_section_id else None
        except ValueError:
            selected_section_id = None

        students_data = []
        if selected_section_id:
            teaches = any(s.id == selected_section_id for s in sections)
            if teaches:
                students = User.objects.filter(role="student", section_id=selected_section_id).order_by("name")
                for s in students:
                    tot, pres, pct = attendance_summary(s.id)
                    status = "Absent"
                    if active_session:
                        att_record = Attendance.objects.filter(user_id=s.id, session_id=active_session.id).first()
                        if att_record:
                            status = att_record.status
                    students_data.append({
                        "id": s.id,
                        "name": s.name,
                        "roll_number": s.roll_number,
                        "department": s.department,
                        "year": s.year,
                        "total": tot,
                        "present": pres,
                        "percentage": pct,
                        "session_status": status,
                    })

        sections_summary = []
        for sec in sections:
            students_in_sec = User.objects.filter(role="student", section_id=sec.id)
            total_students = students_in_sec.count()
            avg_pct = 0.0
            present_in_active = 0

            if total_students > 0:
                pct_sum = 0.0
                for s in students_in_sec:
                    _, _, pct = attendance_summary(s.id)
                    pct_sum += pct
                avg_pct = round(pct_sum / total_students, 1)

                if active_session and active_session.section_id == sec.id:
                    present_in_active = Attendance.objects.filter(session_id=active_session.id, status="Present").count()

            sections_summary.append({
                "id": sec.id,
                "section_name": sec.section_name,
                "year": sec.year,
                "department": sec.department,
                "total_students": total_students,
                "average_attendance": avg_pct,
                "present_in_active": present_in_active,
            })

        return render(
            request,
            "teacher_dashboard.html",
            {
                "user": user,
                "sections": sections,
                "active_session": active_session,
                "active_session_students": active_session_students,
                "active_session_present_count": active_session_present_count,
                "active_session_total_students": active_session_total_students,
                "today_schedule": today_schedule,
                "weekly_schedule": weekly_schedule_list,
                "table_grid": table_grid,
                "students": students_data,
                "selected_section_id": selected_section_id,
                "sections_summary": sections_summary,
            },
        )

    return redirect("student_attendance_dashboard")


def register_user(request):
    if request.session.get("role") != "admin":
        return redirect("login")

    sections = Section.objects.order_by("department", "section_name")

    if request.method == "POST":
        role = request.POST.get("role", "student")
        name = request.POST.get("name", "").strip()
        roll_number = request.POST.get("roll_number", "").strip()
        department = request.POST.get("department", "").strip()
        email = request.POST.get("email", "").strip()
        username = request.POST.get("username", "").strip()
        designation = request.POST.get("designation", "").strip() or None
        section_id = request.POST.get("section_id")
        year_of_study = request.POST.get("year_of_study", "").strip()
        password = request.POST.get("password", "").strip()

        if not section_id:
            section_id = None
        else:
            try:
                section_id = int(section_id)
            except (TypeError, ValueError):
                section_id = None

        valid_files = [f for f in request.FILES.getlist("face_images") if f.name]
        if not valid_files and request.FILES.get("face_image"):
            valid_files = [request.FILES.get("face_image")]

        error_msg = None
        if not all([name, roll_number, department, email, username, password, valid_files]):
            error_msg = "All fields are required, including email, password, and at least one face image."
        elif not is_strong_password(password):
            error_msg = "Choose a stronger password: at least 8 characters with uppercase, lowercase, number, and symbol."
        elif role == "student" and not section_id:
            error_msg = "Please select a section for the student."

        if error_msg:
            messages.error(request, error_msg, extra_tags="danger")
            return render(
                request,
                "register_user.html",
                {
                    "sections": sections,
                    "role": role,
                    "name": name,
                    "email": email,
                    "username": username,
                    "roll_number": roll_number,
                    "department": department,
                    "designation": designation,
                    "section_id": section_id,
                    "year_of_study": year_of_study,
                },
            )

        if role == "student":
            section = Section.objects.filter(id=section_id).first()
            if not section:
                messages.error(request, "Please select a valid section for the student.", extra_tags="danger")
                return render(
                    request,
                    "register_user.html",
                    {
                        "sections": sections,
                        "role": role,
                        "name": name,
                        "email": email,
                        "username": username,
                        "roll_number": roll_number,
                        "department": department,
                        "designation": designation,
                        "section_id": section_id,
                        "year_of_study": year_of_study,
                    },
                )
            year = year_of_study if year_of_study else section.year
        else:
            section = None
            year = None

        existing_user = get_user_by_username(username)
        if existing_user:
            if existing_user.username.lower() == username.lower():
                messages.error(request, "This username is already taken. Choose a different username.", extra_tags="danger")
            elif existing_user.email and existing_user.email.lower() == email.lower():
                messages.error(request, "This email is already registered. Use a different email.", extra_tags="danger")
            else:
                messages.error(
                    request,
                    "A user with this username or email already exists. Please use different credentials.",
                    extra_tags="danger",
                )
            return render(
                request,
                "register_user.html",
                {
                    "sections": sections,
                    "role": role,
                    "name": name,
                    "email": email,
                    "username": username,
                    "roll_number": roll_number,
                    "department": department,
                    "designation": designation,
                    "section_id": section_id,
                    "year_of_study": year_of_study,
                },
            )

        uploaded_images = []
        for image_file in valid_files:
            image_data = image_file.read()
            uploaded_images.append(image_from_base64(base64.b64encode(image_data).decode("utf-8")))

        encoding = get_face_encoding_from_images(uploaded_images)
        if encoding is None:
            messages.error(
                request, "No face detected in the uploaded images. Use clear front-facing photos.", extra_tags="danger"
            )
            return render(
                request,
                "register_user.html",
                {
                    "sections": sections,
                    "role": role,
                    "name": name,
                    "email": email,
                    "username": username,
                    "roll_number": roll_number,
                    "department": department,
                    "designation": designation,
                    "section_id": section_id,
                    "year_of_study": year_of_study,
                },
            )

        if is_duplicate_face(encoding):
            messages.error(
                request,
                "A similar face is already registered. Use a different profile or ask the administrator to verify the existing user.",
                extra_tags="danger",
            )
            return render(
                request,
                "register_user.html",
                {
                    "sections": sections,
                    "role": role,
                    "name": name,
                    "email": email,
                    "username": username,
                    "roll_number": roll_number,
                    "department": department,
                    "designation": designation,
                    "section_id": section_id,
                    "year_of_study": year_of_study,
                },
            )

        try:
            User.objects.create(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role=role,
                name=name,
                roll_number=roll_number,
                department=department,
                designation=designation,
                section_id=section_id,
                year=year,
                face_encoding=serialize_encoding(encoding),
                created_at=datetime.datetime.utcnow().isoformat(),
            )
            messages.success(request, f"{role.title()} registered successfully. Face encoding stored.", extra_tags="success")
            return redirect("register_user")
        except IntegrityError:
            messages.error(request, "Username or email already exists. Choose a different one.", extra_tags="danger")
            return render(
                request,
                "register_user.html",
                {
                    "sections": sections,
                    "role": role,
                    "name": name,
                    "email": email,
                    "username": username,
                    "roll_number": roll_number,
                    "department": department,
                    "designation": designation,
                    "section_id": section_id,
                    "year_of_study": year_of_study,
                },
            )

    return render(request, "register_user.html", {"sections": sections})


def admin_add_schedule(request):
    if request.session.get("role") != "admin":
        return redirect("login")

    sections = Section.objects.order_by("department", "section_name")
    teachers = User.objects.filter(role="teacher").order_by("name")

    if request.method == "POST":
        section_id = request.POST.get("section_id")
        teacher_id = request.POST.get("teacher_id")
        subject = request.POST.get("subject", "").strip()
        day = request.POST.get("day", "").strip()
        start_time = request.POST.get("start_time", "").strip()
        end_time = request.POST.get("end_time", "").strip()
        room = request.POST.get("room", "").strip()
        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")

        error = None
        if not section_id or not teacher_id or not subject or not day or not start_time or not end_time or not room:
            error = "Please fill in all required fields."
        try:
            section_id = int(section_id)
            teacher_id = int(teacher_id)
        except (TypeError, ValueError):
            error = "Invalid section or teacher selection."

        if error:
            messages.error(request, error, extra_tags="danger")
            return render(
                request, "add_schedule.html", {"sections": sections, "teachers": teachers, "form": request.POST}
            )

        ClassSchedule.objects.create(
            section_id=section_id,
            class_name=subject,
            subject=subject,
            day=day,
            start_time=start_time,
            end_time=end_time,
            room=room,
            teacher_id=teacher_id,
            latitude=float(latitude) if latitude else None,
            longitude=float(longitude) if longitude else None,
            created_at=datetime.datetime.utcnow().isoformat(),
        )
        messages.success(request, "Class schedule created successfully.", extra_tags="success")
        return redirect("admin_add_schedule")

    return render(request, "add_schedule.html", {"sections": sections, "teachers": teachers})


def student_attendance_dashboard(request):
    user_id = request.session.get("user_id")
    if not user_id or request.session.get("role") != "student":
        return redirect("login")

    user = get_user_by_id(user_id)
    section = get_student_section(user_id)

    if not section:
        messages.error(request, "You are not assigned to any section.", extra_tags="danger")
        return redirect("dashboard")

    today = datetime.datetime.now().strftime("%a").upper()[:3]
    days_map = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday", "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}
    day_name = days_map.get(today, "MON")

    today_schedule = (
        ClassSchedule.objects.filter(section_id=section.id, day__istartswith=day_name[:3])
        .exclude(subject="Leisure")
        .select_related("teacher")
        .order_by("start_time")
    )
    for s in today_schedule:
        if s.teacher and not s.teacher_name:
            s.teacher_name = s.teacher.name

    weekly_schedule = {}
    all_week_schedule = get_section_schedule(section.id)
    for schedule in all_week_schedule:
        day = schedule.day
        if day not in weekly_schedule:
            weekly_schedule[day] = []
        weekly_schedule[day].append(schedule)

    weekly_schedule_list = list(weekly_schedule.items())

    active_session = get_active_session_for_section(section.id)
    already_marked = False
    if active_session:
        already_marked = Attendance.objects.filter(user_id=user_id, session_id=active_session.id, status="Present").exists()

    total, present, percentage = attendance_summary(user_id)

    recent_attendance = (
        Attendance.objects.filter(user_id=user_id)
        .select_related("session__class_schedule", "session__class_schedule_old")
        .order_by("-recorded_at")[:10]
    )

    stroke_dashoffset = 238.76 - (float(percentage) / 100.0 * 238.76)

    return render(
        request,
        "student_attendance_dashboard.html",
        {
            "user": user,
            "section": section,
            "today_schedule": today_schedule,
            "weekly_schedule": weekly_schedule_list,
            "active_session": active_session,
            "already_marked": already_marked,
            "attendance_percentage": percentage,
            "stroke_dashoffset": stroke_dashoffset,
            "recent_attendance": recent_attendance,
            "geofence_center": GEOFENCE_CENTER,
        },
    )


def mark_attendance_page(request):
    user_id = request.session.get("user_id")
    if not user_id or request.session.get("role") != "student":
        return redirect("login")

    user = get_user_by_id(user_id)
    section = get_student_section(user_id)

    if not section:
        messages.error(request, "You are not assigned to any section.", extra_tags="danger")
        return redirect("dashboard")

    active_session = get_active_session_for_section(section.id)
    if not active_session:
        messages.error(request, "No active attendance session right now.", extra_tags="danger")
        return redirect("student_attendance_dashboard")

    already_marked = Attendance.objects.filter(user_id=user_id, session_id=active_session.id, status="Present").exists()
    if already_marked:
        messages.info(request, "You have already marked your attendance for this session.", extra_tags="success")
        return redirect("student_attendance_dashboard")

    return render(
        request,
        "mark_attendance_page.html",
        {"user": user, "section": section, "session_info": active_session, "geofence_center": GEOFENCE_CENTER},
    )


def mark_attendance(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Method not allowed"}, status=405)

    user_id = request.session.get("user_id")
    if not user_id or request.session.get("role") != "student":
        return JsonResponse({"success": False, "message": "Unauthorized access."}, status=403)

    image1 = request.POST.get("image1")
    image2 = request.POST.get("image2")
    latitude = request.POST.get("latitude")
    longitude = request.POST.get("longitude")

    if not all([image1, image2, latitude, longitude]):
        return JsonResponse({"success": False, "message": "Missing attendance data."}, status=400)

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid location coordinates."}, status=400)

    student = get_user_by_id(user_id)
    section = get_student_section(user_id)

    if not section:
        return JsonResponse({"success": False, "message": "You are not assigned to any section."}, status=403)

    active_session = get_active_session_for_section(section.id)
    if active_session is None:
        return JsonResponse(
            {"success": False, "message": "Attendance is not open yet. Wait for your teacher to start the session."},
            status=400,
        )

    if active_session.latitude is None or active_session.longitude is None:
        return JsonResponse(
            {
                "success": False,
                "message": "Active session location is not available. Please ask your teacher to restart the session.",
            },
            status=400,
        )

    distance_to_teacher = calculate_distance(latitude, longitude, active_session.latitude, active_session.longitude)
    if distance_to_teacher > 200:
        return JsonResponse(
            {
                "success": False,
                "message": f"You are not near the classroom. Move closer (currently {int(distance_to_teacher)}m away).",
            },
            status=400,
        )

    frame1 = image_from_base64(image1)
    if frame1 is None:
        return JsonResponse({"success": False, "message": "Unable to decode camera frame."}, status=400)

    known_encodings = []
    students = User.objects.filter(role="student").exclude(face_encoding__isnull=True).exclude(face_encoding="")
    for s in students:
        known_encodings.append((s.id, deserialize_encoding(s.face_encoding)))

    rgb = cv2.cvtColor(frame1, cv2.COLOR_BGR2RGB)
    face_locations = face_recognition.face_locations(rgb, model="hog")
    if not face_locations:
        return JsonResponse({"success": False, "message": "No face detected in the captured image."}, status=400)

    # Perform texture-based liveness / anti-spoofing detection
    is_real, spoof_label, score = is_liveness_valid_v2(frame1, face_locations[0])
    if not is_real:
        return JsonResponse(
            {
                "success": False,
                "message": "Face spoofing/presentation attack detected (printed photo, screen, or replay). Please use your real face.",
            },
            status=400,
        )

    encodings = face_recognition.face_encodings(rgb, face_locations)
    if not encodings:
        return JsonResponse({"success": False, "message": "Unable to extract face features from the image."}, status=400)
    target_encoding = encodings[0]

    face_distances = [
        face_recognition.face_distance([encoding], target_encoding)[0] for _, encoding in known_encodings
    ]
    if not face_distances:
        return JsonResponse(
            {"success": False, "message": "No registered student faces available for recognition."}, status=400
        )

    best_index = int(np.argmin(face_distances))
    best_distance = face_distances[best_index]
    matched_id = known_encodings[best_index][0]
    threshold = 0.48

    if best_distance > threshold:
        return JsonResponse(
            {
                "success": False,
                "message": "Your face does not match your registered profile. Please try again with better lighting and a clear front-facing image.",
            },
            status=400,
        )

    if matched_id != user_id:
        matched_user = User.objects.filter(id=matched_id).first()
        matched_name = matched_user.name if matched_user else "another student"
        return JsonResponse(
            {"success": False, "message": f"Wrong person detected - matched to {matched_name}. Please use your own face."},
            status=400,
        )

    now = datetime.datetime.now()
    date = now.date().isoformat()
    time = now.strftime("%H:%M")

    attendance_record = Attendance.objects.filter(user_id=user_id, session_id=active_session.id).first()
    if attendance_record:
        attendance_record.status = "Present"
        attendance_record.date = date
        attendance_record.time = time
        attendance_record.location = f"{latitude:.6f},{longitude:.6f}"
        attendance_record.latitude = latitude
        attendance_record.longitude = longitude
        attendance_record.recorded_at = now.isoformat()
        attendance_record.save()
    else:
        Attendance.objects.create(
            user_id=user_id,
            session_id=active_session.id,
            date=date,
            time=time,
            status="Present",
            location=f"{latitude:.6f},{longitude:.6f}",
            latitude=latitude,
            longitude=longitude,
            recorded_at=now.isoformat(),
        )

    return JsonResponse({"success": True, "message": "Attendance marked successfully.", "date": date, "time": time})


def start_session(request):
    if request.method != "POST":
        return redirect("dashboard")

    if request.session.get("role") != "teacher":
        return redirect("login")

    class_schedule_id = request.POST.get("class_id")
    latitude = request.POST.get("latitude")
    longitude = request.POST.get("longitude")

    if not all([class_schedule_id, latitude, longitude]):
        messages.error(request, "Class selection and your current location are required.", extra_tags="danger")
        return redirect("dashboard")

    try:
        class_schedule_id = int(class_schedule_id)
        latitude = float(latitude)
        longitude = float(longitude)
    except ValueError:
        messages.error(request, "Invalid class or location values.", extra_tags="danger")
        return redirect("dashboard")

    teacher_id = request.session["user_id"]
    can_start, message = can_teacher_start_session(teacher_id, class_schedule_id)
    if not can_start:
        messages.error(request, message, extra_tags="danger")
        return redirect("dashboard")

    class_row = ClassSchedule.objects.filter(id=class_schedule_id).first()
    if class_row is None:
        messages.error(request, "Selected class does not exist.", extra_tags="danger")
        return redirect("dashboard")

    now = datetime.datetime.utcnow()
    try:
        end_h, end_m = map(int, class_row.end_time.split(":"))
        local_now = datetime.datetime.now()
        local_expiry = local_now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

        offset = datetime.datetime.now() - datetime.datetime.utcnow()
        expires = local_expiry - offset

        if expires <= datetime.datetime.utcnow() + datetime.timedelta(minutes=5):
            expires = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
    except Exception:
        expires = now + datetime.timedelta(minutes=25)

    try:
        sess = AttendanceSession.objects.create(
            class_schedule_old_id=class_schedule_id,  # class_id column compatibility
            class_schedule_id=class_schedule_id,  # class_schedule_id column
            section_id=class_row.section_id,
            teacher_id=teacher_id,
            started_at=now.isoformat(),
            expires_at=expires.isoformat(),
            latitude=latitude,
            longitude=longitude,
            is_active=1,
            created_at=now.isoformat(),
        )

        # Pre-populate Attendance records with 'Absent' status for all students in the section
        students = User.objects.filter(section_id=class_row.section_id, role="student")
        local_now = datetime.datetime.now()
        date_str = local_now.date().isoformat()
        time_str = local_now.strftime("%H:%M")
        for student in students:
            Attendance.objects.create(
                user_id=student.id,
                session_id=sess.id,
                date=date_str,
                time=time_str,
                status="Absent",
                location="Session started",
                latitude=None,
                longitude=None,
                recorded_at=local_now.isoformat(),
            )

        teacher = get_user_by_id(teacher_id)
        notify_section_attendance_started(
            section_id=class_row.section_id,
            session_id=sess.id,
            subject=class_row.subject,
            teacher_name=teacher.name,
        )

        messages.success(
            request, f"Attendance session started for {class_row.subject}. Students notified!", extra_tags="success"
        )
    except Exception as e:
        messages.error(request, f"Error starting session: {str(e)}", extra_tags="danger")

    return redirect("dashboard")


def stop_session(request):
    if request.method != "POST":
        return redirect("dashboard")

    if request.session.get("role") != "teacher":
        return redirect("login")

    session_id = request.POST.get("session_id")
    if not session_id:
        messages.error(request, "Session id required.", extra_tags="danger")
        return redirect("dashboard")

    try:
        session_id = int(session_id)
    except ValueError:
        messages.error(request, "Invalid session id.", extra_tags="danger")
        return redirect("dashboard")

    AttendanceSession.objects.filter(id=session_id).update(is_active=0)
    messages.success(request, "Attendance session stopped.", extra_tags="success")
    return redirect("dashboard")


def teacher_mark_manual(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Method not allowed"}, status=405)

    if request.session.get("role") != "teacher":
        return JsonResponse({"success": False, "message": "Unauthorized access."}, status=403)

    student_id = request.POST.get("student_id")
    session_id = request.POST.get("session_id")

    if not student_id or not session_id:
        return JsonResponse({"success": False, "message": "Missing student or session information."}, status=400)

    try:
        student_id = int(student_id)
        session_id = int(session_id)
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid IDs."}, status=400)

    existing = Attendance.objects.filter(user_id=student_id, session_id=session_id).first()
    if existing and existing.status == "Present":
        return JsonResponse({"success": False, "message": "Attendance is already marked for this student."}, status=400)

    sess = AttendanceSession.objects.filter(id=session_id).first()
    if not sess:
        return JsonResponse({"success": False, "message": "Attendance session not found."}, status=404)

    now = datetime.datetime.now()
    date = now.date().isoformat()
    time = now.strftime("%H:%M")

    try:
        if existing:
            existing.status = "Present"
            existing.date = date
            existing.time = time
            existing.location = "Marked manually by Teacher"
            existing.recorded_at = now.isoformat()
            existing.save()
        else:
            Attendance.objects.create(
                user_id=student_id,
                session_id=session_id,
                date=date,
                time=time,
                status="Present",
                location="Marked manually by Teacher",
                recorded_at=now.isoformat(),
            )
        return JsonResponse({"success": True, "message": "Attendance marked successfully."})
    except Exception as e:
        return JsonResponse({"success": False, "message": f"Database error: {str(e)}"}, status=500)


def attendance_logs(request):
    if request.session.get("role") != "admin":
        return redirect("login")

    selected_section_id = request.GET.get("section_id")
    try:
        selected_section_id = int(selected_section_id) if selected_section_id else None
    except ValueError:
        selected_section_id = None

    sections = Section.objects.all().order_by("department", "section_name")

    logs_query = Attendance.objects.select_related("user__section").order_by("user__section__section_name", "-id")
    if selected_section_id:
        logs_query = logs_query.filter(user__section_id=selected_section_id)

    return render(
        request,
        "attendance_logs.html",
        {
            "logs": logs_query,
            "sections": sections,
            "selected_section_id": selected_section_id,
        },
    )


def reports(request):
    if request.session.get("role") != "admin":
        return redirect("login")

    selected_section_id = request.GET.get("section_id")
    try:
        selected_section_id = int(selected_section_id) if selected_section_id else None
    except ValueError:
        selected_section_id = None

    sections = Section.objects.all().order_by("department", "section_name")

    students = User.objects.filter(role="student").select_related("section").order_by("section__section_name", "name")
    if selected_section_id:
        students = students.filter(section_id=selected_section_id)

    report_rows = []
    for student in students:
        total, present, percentage = attendance_summary(student.id)
        report_rows.append({
            "name": student.name,
            "roll_number": student.roll_number,
            "department": student.department,
            "section_name": student.section.section_name if student.section else "Unassigned",
            "present": present,
            "total": total,
            "percentage": percentage,
        })
    low_attendance = [row for row in report_rows if row["percentage"] < 75]

    return render(
        request,
        "reports.html",
        {
            "report_rows": report_rows,
            "low_attendance": low_attendance,
            "sections": sections,
            "selected_section_id": selected_section_id,
        },
    )


def delete_student(request, student_id):
    if request.method != "POST":
        return redirect("dashboard")

    if request.session.get("role") != "admin":
        messages.error(request, "You do not have permission to delete students.", extra_tags="danger")
        return redirect("dashboard")

    student = User.objects.filter(id=student_id, role="student").first()
    if not student:
        messages.error(request, "Student not found.", extra_tags="danger")
        return redirect("dashboard")

    try:
        # Delete attendance records for student
        Attendance.objects.filter(user_id=student_id).delete()
        # Delete user
        student.delete()
        messages.success(
            request,
            f"Student '{student.name}' ({student.username}) has been successfully deleted.",
            extra_tags="success",
        )
    except Exception as e:
        messages.error(request, f"Error deleting student: {str(e)}", extra_tags="danger")

    return redirect("dashboard")


def delete_schedule(request, schedule_id):
    if request.method != "POST":
        return redirect("dashboard")

    if request.session.get("role") != "admin":
        messages.error(request, "You do not have permission to delete schedules.", extra_tags="danger")
        return redirect("dashboard")

    schedule = ClassSchedule.objects.filter(id=schedule_id).first()
    if not schedule:
        messages.error(request, "Schedule not found.", extra_tags="danger")
        return redirect("dashboard")

    try:
        subject = schedule.subject
        teacher_name = schedule.teacher.name if schedule.teacher else "Unknown Teacher"
        
        # Delete related attendance sessions to avoid SQLite immediate foreign key constraint failures
        AttendanceSession.objects.filter(class_schedule_id=schedule.id).delete()
        AttendanceSession.objects.filter(class_schedule_old_id=schedule.id).delete()
        
        schedule.delete()
        messages.success(
            request,
            f"Schedule for '{subject}' (Teacher: {teacher_name}) has been successfully deleted.",
            extra_tags="success",
        )
    except Exception as e:
        messages.error(request, f"Error deleting schedule: {str(e)}", extra_tags="danger")

    return redirect("dashboard")


def admin_reset_password(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Method not allowed"}, status=405)

    if request.session.get("role") != "admin":
        return JsonResponse({"success": False, "message": "Unauthorized"}, status=403)

    teacher_id = None
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body)
            teacher_id = data.get("teacher_id")
        except Exception:
            pass
    else:
        teacher_id = request.POST.get("teacher_id")

    if not teacher_id:
        return JsonResponse({"success": False, "message": "Missing teacher_id"}, status=400)

    try:
        teacher_id = int(teacher_id)
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid teacher_id"}, status=400)

    teacher = User.objects.filter(id=teacher_id, role="teacher").first()
    if not teacher:
        return JsonResponse({"success": False, "message": "Teacher not found"}, status=404)

    def _generate_temp_password(length=12):
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
        while True:
            pwd = "".join(secrets.choice(alphabet) for _ in range(length))
            if is_strong_password(pwd):
                return pwd

    temp_password = _generate_temp_password(12)
    try:
        teacher.password_hash = hash_password(temp_password)
        teacher.save()
        return JsonResponse({"success": True, "username": teacher.username, "password": temp_password})
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=500)


# ============ NOTIFICATION API ENDPOINTS ============


def get_notifications(request):
    user_id = request.session.get("user_id")
    if not user_id:
        return JsonResponse({"success": False, "message": "Not authenticated"}, status=403)

    notifications = get_unread_notifications(user_id, limit=20)
    return JsonResponse({
        "success": True,
        "count": len(notifications),
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "data": json.loads(n.data) if n.data else None,
                "created_at": n.created_at,
            }
            for n in notifications
        ],
    })


def mark_notification_as_read(request, notification_id):
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "Method not allowed"}, status=405)

    if not request.session.get("user_id"):
        return JsonResponse({"success": False, "message": "Not authenticated"}, status=403)

    try:
        mark_notification_read(notification_id)
        return JsonResponse({"success": True, "message": "Notification marked as read"})
    except Exception as e:
        return JsonResponse({"success": False, "message": str(e)}, status=400)


def get_all_notifications(request):
    user_id = request.session.get("user_id")
    if not user_id:
        return JsonResponse({"success": False, "message": "Not authenticated"}, status=403)

    notifications = Notification.objects.filter(user_id=user_id).order_by("-created_at")[:50]
    return JsonResponse({
        "success": True,
        "count": len(notifications),
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "data": json.loads(n.data) if n.data else None,
                "is_read": n.is_read,
                "created_at": n.created_at,
            }
            for n in notifications
        ],
    })


# Debug endpoint to list class_schedule rows
def admin_debug_class_schedule(request):
    if not DEBUG and request.session.get("role") != "admin":
        return JsonResponse({"success": False, "message": "Forbidden"}, status=403)

    rows = ClassSchedule.objects.all().order_by("id")
    data = []
    for r in rows:
        data.append({
            "id": r.id,
            "class_name": r.class_name,
            "subject": r.subject,
            "day": r.day,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "room": r.room,
            "teacher_id": r.teacher_id,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "created_at": r.created_at,
            "section_id": r.section_id,
        })
    return JsonResponse({"success": True, "rows": data})


from django.http import HttpResponse

def teacher_download_register_csv(request):
    if request.session.get("role") not in ["teacher", "admin"]:
        return HttpResponse("Unauthorized", status=403)
        
    section_id = request.GET.get("section_id")
    if not section_id:
        return HttpResponse("Section ID is required", status=400)
        
    try:
        section_id = int(section_id)
    except ValueError:
        return HttpResponse("Invalid Section ID", status=400)
        
    section = Section.objects.filter(id=section_id).first()
    if not section:
        return HttpResponse("Section not found", status=404)
        
    # If teacher, verify they teach this section
    if request.session.get("role") == "teacher":
        sections = get_teacher_sections(request.session["user_id"])
        if not any(s.id == section_id for s in sections):
            return HttpResponse("Forbidden", status=403)
            
    students = User.objects.filter(role="student", section_id=section_id).order_by("name")
    
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="register_{section.section_name}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(["Roll Number", "Student Name", "Department", "Year", "Present Classes", "Total Classes", "Attendance Percentage"])
    
    for s in students:
        tot, pres, pct = attendance_summary(s.id)
        writer.writerow([
            s.roll_number or "N/A",
            s.name or s.username,
            s.department or "N/A",
            s.year or "N/A",
            pres,
            tot,
            f"{pct}%"
        ])
    return response


def admin_download_logs_csv(request):
    if request.session.get("role") != "admin":
        return HttpResponse("Unauthorized", status=403)
        
    section_id = request.GET.get("section_id")
    logs = Attendance.objects.select_related("user__section", "session__class_schedule").order_by("-recorded_at")
    
    if section_id:
        try:
            section_id = int(section_id)
            logs = logs.filter(user__section_id=section_id)
        except ValueError:
            pass
            
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="attendance_logs.csv"'
    
    writer = csv.writer(response)
    writer.writerow(["Student Name", "Roll Number", "Section", "Date", "Time", "Status", "Location", "Recorded At"])
    
    for log in logs:
        writer.writerow([
            log.user.name or log.user.username,
            log.user.roll_number or "N/A",
            log.user.section.section_name if log.user.section else "Unassigned",
            log.date,
            log.time,
            log.status,
            log.location or "N/A",
            log.recorded_at
        ])
    return response


def admin_download_reports_csv(request):
    if request.session.get("role") != "admin":
        return HttpResponse("Unauthorized", status=403)
        
    section_id = request.GET.get("section_id")
    students = User.objects.filter(role="student").select_related("section").order_by("section__section_name", "name")
    
    if section_id:
        try:
            section_id = int(section_id)
            students = students.filter(section_id=section_id)
        except ValueError:
            pass
            
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="attendance_summary_report.csv"'
    
    writer = csv.writer(response)
    writer.writerow(["Student Name", "Roll Number", "Department", "Section", "Present Classes", "Total Classes", "Attendance Percentage"])
    
    for s in students:
        tot, pres, pct = attendance_summary(s.id)
        writer.writerow([
            s.name or s.username,
            s.roll_number or "N/A",
            s.department or "N/A",
            s.section.section_name if s.section else "Unassigned",
            pres,
            tot,
            f"{pct}%"
        ])
    return response

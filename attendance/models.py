from django.db import models


class Section(models.Model):
    section_name = models.CharField(max_length=100, unique=True)
    department = models.CharField(max_length=100)
    year = models.CharField(max_length=50)
    created_at = models.CharField(max_length=100)  # ISO string

    class Meta:
        db_table = "sections"

    def __str__(self):
        return f"{self.section_name} ({self.year} - {self.department})"


class User(models.Model):
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    password_hash = models.CharField(max_length=256)
    role = models.CharField(max_length=50)  # 'admin', 'teacher', 'student'
    name = models.CharField(max_length=150, null=True, blank=True)
    roll_number = models.CharField(max_length=50, null=True, blank=True)
    department = models.CharField(max_length=150, null=True, blank=True)
    designation = models.CharField(max_length=150, null=True, blank=True)
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="section_id",
        related_name="students",
    )
    year = models.CharField(max_length=50, null=True, blank=True)
    face_encoding = models.TextField(null=True, blank=True)
    created_at = models.CharField(max_length=100)  # ISO string

    class Meta:
        db_table = "users"

    @property
    def section_name(self):
        return self.section.section_name if self.section else ""

    def __str__(self):
        return f"{self.name or self.username} ({self.role})"


class ClassSchedule(models.Model):
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        db_column="section_id",
        related_name="schedules",
    )
    class_name = models.CharField(max_length=150, default="")
    subject = models.CharField(max_length=150)
    day = models.CharField(max_length=20)
    start_time = models.CharField(max_length=10)  # e.g., "09:40"
    end_time = models.CharField(max_length=10)  # e.g., "10:40"
    room = models.CharField(max_length=50)
    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="teacher_id",
        related_name="schedules",
        null=True,
        blank=True,
    )
    teacher_name = models.CharField(max_length=150, null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    created_at = models.CharField(max_length=100)  # ISO string

    class Meta:
        db_table = "class_schedule"

    @property
    def section_name(self):
        return self.section.section_name if self.section else ""

    @property
    def year(self):
        return self.section.year if self.section else ""

    @property
    def department(self):
        return self.section.department if self.section else ""

    def __str__(self):
        return f"{self.subject} ({self.day} {self.start_time}-{self.end_time})"


class AttendanceSession(models.Model):
    class_schedule_old = models.ForeignKey(
        ClassSchedule,
        on_delete=models.CASCADE,
        db_column="class_id",
        related_name="sessions_legacy",
        null=True,
        blank=True,
    )
    class_schedule = models.ForeignKey(
        ClassSchedule,
        on_delete=models.CASCADE,
        db_column="class_schedule_id",
        related_name="sessions",
        null=True,
        blank=True,
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        db_column="section_id",
        related_name="sessions",
        null=True,
        blank=True,
    )
    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="teacher_id",
        related_name="sessions",
    )
    started_at = models.CharField(max_length=100)  # ISO string
    expires_at = models.CharField(max_length=100)  # ISO string
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_active = models.IntegerField(default=1)  # 1 = True, 0 = False
    created_at = models.CharField(max_length=100)  # ISO string

    class Meta:
        db_table = "attendance_sessions"

    @property
    def subject(self):
        if self.class_schedule:
            return self.class_schedule.subject
        if self.class_schedule_old:
            return self.class_schedule_old.subject
        return ""

    @property
    def room(self):
        if self.class_schedule:
            return self.class_schedule.room
        if self.class_schedule_old:
            return self.class_schedule_old.room
        return ""

    @property
    def start_time(self):
        if self.class_schedule:
            return self.class_schedule.start_time
        if self.class_schedule_old:
            return self.class_schedule_old.start_time
        return ""

    @property
    def end_time(self):
        if self.class_schedule:
            return self.class_schedule.end_time
        if self.class_schedule_old:
            return self.class_schedule_old.end_time
        return ""

    @property
    def section_name(self):
        return self.section.section_name if self.section else ""

    @property
    def teacher_name(self):
        return self.teacher.name if self.teacher else ""

    def __str__(self):
        return f"Session for {self.class_schedule or self.class_schedule_old} (Active: {self.is_active})"


class Attendance(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="attendances",
    )
    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="session_id",
        related_name="attendances",
    )
    date = models.CharField(max_length=20)  # YYYY-MM-DD
    time = models.CharField(max_length=20)  # HH:MM AM/PM
    status = models.CharField(max_length=50)  # e.g., "Present", "Absent"
    location = models.CharField(max_length=150, null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    recorded_at = models.CharField(max_length=100)  # ISO string

    class Meta:
        db_table = "attendance"

    @property
    def name(self):
        return self.user.name if self.user else ""

    @property
    def roll_number(self):
        return self.user.roll_number if self.user else ""

    @property
    def subject(self):
        return self.session.subject if self.session else ""

    @property
    def room(self):
        return self.session.room if self.session else ""

    def __str__(self):
        return f"{self.user.username} - {self.date} {self.time} ({self.status})"


class Notification(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="notifications",
    )
    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="session_id",
        related_name="notifications",
    )
    type = models.CharField(max_length=50)  # e.g., "attendance_started", "info"
    title = models.CharField(max_length=150)
    message = models.TextField()
    data = models.TextField(null=True, blank=True)  # JSON string
    is_read = models.IntegerField(default=0)  # 0 = Unread, 1 = Read
    created_at = models.CharField(max_length=100)  # ISO string

    class Meta:
        db_table = "notifications"

    def __str__(self):
        return f"Notification for {self.user.username}: {self.title}"

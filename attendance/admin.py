from django.contrib import admin
from .models import Section, User, ClassSchedule, AttendanceSession, Attendance, Notification

admin.site.register(Section)
admin.site.register(User)
admin.site.register(ClassSchedule)
admin.site.register(AttendanceSession)
admin.site.register(Attendance)
admin.site.register(Notification)

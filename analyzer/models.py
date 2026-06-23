from django.db import models


class ProcessedReport(models.Model):
    source_filename = models.CharField(max_length=255)
    teacher_name = models.CharField(max_length=180, blank=True)
    school = models.CharField(max_length=255)
    class_name = models.CharField(max_length=160)
    year = models.CharField(max_length=4)
    bimester = models.CharField(max_length=12)
    bimester_label = models.CharField(max_length=40)
    processed_at = models.DateTimeField(auto_now_add=True)

    total_students = models.PositiveIntegerField(default=0)
    class_average = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    freq_media = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    risk_count = models.PositiveIntegerField(default=0)
    risk_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    learning_index = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    raw_data = models.JSONField(default=dict, blank=True)
    metrics_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-processed_at']
        indexes = [
            models.Index(fields=['school', 'class_name', 'year', 'bimester']),
            models.Index(fields=['processed_at']),
        ]

    def __str__(self):
        return f'{self.class_name} - {self.bimester_label} - {self.processed_at:%d/%m/%Y %H:%M}'


class StudentSnapshot(models.Model):
    report = models.ForeignKey(ProcessedReport, on_delete=models.CASCADE, related_name='students')
    num = models.PositiveIntegerField(null=True, blank=True)
    name = models.CharField(max_length=220)
    ra = models.CharField(max_length=40, blank=True)
    active = models.BooleanField(default=True)
    total_faltas = models.PositiveIntegerField(null=True, blank=True)
    average = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    level_key = models.CharField(max_length=20, blank=True)
    level_label = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['ra']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name


class StudentGrade(models.Model):
    student = models.ForeignKey(StudentSnapshot, on_delete=models.CASCADE, related_name='grades')
    subject_code = models.CharField(max_length=12)
    subject_name = models.CharField(max_length=80)
    grade = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    class Meta:
        ordering = ['subject_code']
        indexes = [
            models.Index(fields=['subject_code']),
        ]

    def __str__(self):
        return f'{self.student.name} - {self.subject_code}: {self.grade}'


class SubjectSnapshot(models.Model):
    report = models.ForeignKey(ProcessedReport, on_delete=models.CASCADE, related_name='subjects')
    code = models.CharField(max_length=12)
    name = models.CharField(max_length=80)
    average = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    level_key = models.CharField(max_length=20, blank=True)
    level_label = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ['code']
        indexes = [
            models.Index(fields=['code']),
        ]

    def __str__(self):
        return f'{self.code} - {self.average}'

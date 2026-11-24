from django.conf import settings
from django.db import models


class Workspace(models.Model):
    company_name = models.CharField(max_length=150)
    site = models.CharField(max_length=150, blank=True)
    product_category = models.CharField(max_length=150, blank=True)
    jurisdiction = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.company_name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        REVIEWER = 'reviewer', 'Reviewer'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workspace_memberships')
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.REVIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('user', 'workspace'), name='unique_user_workspace_membership'),
        ]

    def __str__(self):
        return f'{self.user} — {self.workspace} ({self.get_role_display()})'

import re
from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import UserProfile
from apps.workspaces.models import Workspace, Membership


class RegisterForm(forms.Form):
    full_name = forms.CharField(
        max_length=150,
        required=True,
        error_messages={'required': 'Full name is required.'},
        widget=forms.TextInput(
            attrs={
                'class': 'form-input',
                'id': 'id_full_name',
                'autocomplete': 'name',
                'spellcheck': 'false',
            }
        ),
    )
    work_email = forms.EmailField(
        max_length=254,
        required=True,
        error_messages={
            'required': 'Work email is required.',
            'invalid': 'Please enter a valid email address.',
        },
        widget=forms.EmailInput(
            attrs={
                'class': 'form-input',
                'id': 'id_work_email',
                'autocomplete': 'email',
                'spellcheck': 'false',
                'autocapitalize': 'none',
            }
        ),
    )
    company_name = forms.CharField(
        max_length=150,
        required=True,
        error_messages={'required': 'Company name is required.'},
        widget=forms.TextInput(
            attrs={
                'class': 'form-input input-company',
                'id': 'id_company_name',
                'autocomplete': 'organization',
                'spellcheck': 'false',
            }
        ),
    )
    password = forms.CharField(
        required=True,
        error_messages={'required': 'Password is required.'},
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-input',
                'id': 'id_password',
                'autocomplete': 'new-password',
            }
        ),
    )
    confirm_password = forms.CharField(
        required=True,
        error_messages={'required': 'Confirm password is required.'},
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-input',
                'id': 'id_confirm_password',
                'autocomplete': 'new-password',
            }
        ),
    )
    compliance_ack = forms.BooleanField(
        required=True,
        error_messages={
            'required': 'You must acknowledge that your actions are recorded and attributable.'
        },
        widget=forms.CheckboxInput(
            attrs={
                'class': 'compliance-checkbox-input',
                'id': 'id_compliance_ack',
            }
        ),
    )

    def clean_work_email(self):
        email = self.cleaned_data.get('work_email', '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account with this email address already exists.')
        return email

    def clean_password(self):
        password = self.cleaned_data.get('password')
        if password:
            try:
                validate_password(password)
            except ValidationError as e:
                raise ValidationError(list(e.messages))
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password and confirm_password:
            if password != confirm_password:
                self.add_error('confirm_password', 'Passwords do not match.')

        return cleaned_data

    def generate_username(self, full_name, email):
        cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', full_name).strip().lower()
        parts = cleaned.split()
        if len(parts) >= 2:
            base = f"{parts[0]}.{parts[-1]}"
        elif len(parts) == 1:
            base = parts[0]
        else:
            base = email.split('@')[0].lower()
            base = re.sub(r'[^a-zA-Z0-9._]', '', base)
            if not base:
                base = 'user'

        username = base[:140]
        candidate = username
        counter = 1
        while User.objects.filter(username=candidate).exists():
            candidate = f"{username[:135]}{counter}"
            counter += 1
        return candidate

    @transaction.atomic
    def save(self):
        full_name = self.cleaned_data['full_name'].strip()
        email = self.cleaned_data['work_email']
        company_name = self.cleaned_data['company_name'].strip()
        password = self.cleaned_data['password']

        parts = full_name.split(maxsplit=1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''

        username = self.generate_username(full_name, email)

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        UserProfile.objects.create(
            user=user,
            full_name=full_name,
            company_name=company_name,
            compliance_acknowledged=True,
        )

        workspace = Workspace.objects.create(company_name=company_name)
        Membership.objects.create(user=user, workspace=workspace, role=Membership.Role.OWNER)

        return user


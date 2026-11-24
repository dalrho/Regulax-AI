from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import Membership, Workspace


class WorkspaceSettingsForm(forms.ModelForm):
    class Meta:
        model = Workspace
        fields = ('company_name', 'site', 'product_category', 'jurisdiction')
        widgets = {
            'company_name': forms.TextInput(attrs={'autocomplete': 'organization'}),
            'site': forms.TextInput(attrs={'autocomplete': 'off'}),
            'product_category': forms.TextInput(attrs={'autocomplete': 'off'}),
            'jurisdiction': forms.TextInput(attrs={'autocomplete': 'off'}),
        }


class AddMemberForm(forms.Form):
    email = forms.EmailField(label='Existing account email')
    role = forms.ChoiceField(choices=Membership.Role.choices)

    def __init__(self, *args, workspace, **kwargs):
        self.workspace = workspace
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            raise ValidationError('No existing account uses that email. Invitations are not enabled yet.')
        if Membership.objects.filter(user=user, workspace=self.workspace).exists():
            raise ValidationError('That account is already a workspace member.')
        self.user = user
        return email

    def save(self):
        return Membership.objects.create(
            user=self.user,
            workspace=self.workspace,
            role=self.cleaned_data['role'],
        )

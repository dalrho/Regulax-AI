from django import forms
from django.contrib.auth import authenticate


class LoginForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(
            attrs={
                'class': 'form-input',
                'placeholder': 'firstname.lastname',
                'autocomplete': 'username',
                'id': 'id_username',
            }
        ),
    )
    password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-input',
                'placeholder': 'Enter your password',
                'autocomplete': 'current-password',
                'id': 'id_password',
            }
        ),
    )
    remember_username = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(
            attrs={
                'class': 'form-checkbox',
                'id': 'id_remember_username',
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get('username')
        password = cleaned_data.get('password')

        if username and password:
            self.user = authenticate(username=username, password=password)
            if self.user is None:
                raise forms.ValidationError(
                    "Invalid username or password. Please try again."
                )
            if not self.user.is_active:
                raise forms.ValidationError("This account is currently disabled.")
        return cleaned_data


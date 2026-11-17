from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from .models import UserProfile


class RegisterViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.register_url = reverse('register')
        self.login_url = reverse('login')
        self.dashboard_url = reverse('dashboard')

    def test_register_page_renders_correctly(self):
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'register/register.html')
        self.assertTemplateUsed(response, 'base/base.html')

        content = response.content.decode('utf-8')
        # Brand & Left pane elements
        self.assertIn('Regulax AI', content)
        self.assertIn('Create', content)
        self.assertIn('Account', content)
        self.assertIn('Accounts are', content)
        self.assertIn('never', content)
        self.assertIn('shared', content)

        # Form fields & labels
        self.assertIn('Full name', content)
        self.assertIn('Work email', content)
        self.assertIn('Company name', content)
        self.assertIn('Password', content)
        self.assertIn('Confirm password', content)

        # Compliance statement
        self.assertIn('I understand that my actions are recorded and attributable to me', content)
        self.assertIn('electronic signature is binding', content)

        # Button & Link
        self.assertIn('Submit request', content)
        self.assertIn('Already approved?', content)
        self.assertIn('Sign in', content)

    def test_successful_registration(self):
        data = {
            'full_name': 'Alex Mercer',
            'work_email': 'alex.mercer@biotechlabs.com',
            'company_name': 'Biotech Labs Inc',
            'password': 'SecurePassword123!',
            'confirm_password': 'SecurePassword123!',
            'compliance_ack': 'on',
        }
        response = self.client.post(self.register_url, data, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.login_url)

        # Verify user was created
        user = User.objects.filter(email='alex.mercer@biotechlabs.com').first()
        self.assertIsNotNone(user)
        self.assertEqual(user.first_name, 'Alex')
        self.assertEqual(user.last_name, 'Mercer')
        self.assertTrue(user.check_password('SecurePassword123!'))

        # Verify UserProfile was created
        profile = UserProfile.objects.filter(user=user).first()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.full_name, 'Alex Mercer')
        self.assertEqual(profile.company_name, 'Biotech Labs Inc')
        self.assertTrue(profile.compliance_acknowledged)

        # Verify login works with the newly created account (both username and email)
        login_success = self.client.login(username=user.username, password='SecurePassword123!')
        self.assertTrue(login_success)

    def test_registration_with_email_login(self):
        data = {
            'full_name': 'Sarah Connor',
            'work_email': 'sarah@cyberdyne.io',
            'company_name': 'Cyberdyne',
            'password': 'StrongPassword2026!',
            'confirm_password': 'StrongPassword2026!',
            'compliance_ack': 'on',
        }
        response = self.client.post(self.register_url, data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Attempt login via work email using the login form
        login_response = self.client.post(
            self.login_url,
            {'username': 'sarah@cyberdyne.io', 'password': 'StrongPassword2026!'},
            follow=False
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertRedirects(login_response, self.dashboard_url)

    def test_registration_password_mismatch(self):
        data = {
            'full_name': 'John Doe',
            'work_email': 'john.doe@example.com',
            'company_name': 'Acme',
            'password': 'Password12345!',
            'confirm_password': 'DifferentPassword12345!',
            'compliance_ack': 'on',
        }
        response = self.client.post(self.register_url, data, follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'confirm_password', 'Passwords do not match.')
        self.assertFalse(User.objects.filter(email='john.doe@example.com').exists())

    def test_registration_duplicate_email(self):
        User.objects.create_user(
            username='existing.user',
            email='existing@example.com',
            password='Password123!'
        )
        data = {
            'full_name': 'Existing Duplicate',
            'work_email': 'existing@example.com',
            'company_name': 'Acme',
            'password': 'Password12345!',
            'confirm_password': 'Password12345!',
            'compliance_ack': 'on',
        }
        response = self.client.post(self.register_url, data, follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'work_email', 'An account with this email address already exists.')

    def test_registration_missing_compliance(self):
        data = {
            'full_name': 'No Compliance',
            'work_email': 'nocompliance@example.com',
            'company_name': 'Acme',
            'password': 'Password12345!',
            'confirm_password': 'Password12345!',
            # compliance_ack omitted
        }
        response = self.client.post(self.register_url, data, follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'],
            'compliance_ack',
            'You must acknowledge that your actions are recorded and attributable.'
        )

    def test_authenticated_user_redirected(self):
        user = User.objects.create_user(username='logged.in', password='Password123!')
        self.client.login(username='logged.in', password='Password123!')
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.dashboard_url)

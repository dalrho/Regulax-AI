from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User


class LoginViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.username = 'jane.doe'
        self.password = 'Password123!'
        self.user = User.objects.create_user(
            username=self.username,
            password=self.password,
            email='jane.doe@example.com'
        )

    def test_login_page_renders_correctly(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'login/login.html')
        self.assertTemplateUsed(response, 'base/base.html')

        # Check key content from the design
        content = response.content.decode('utf-8')
        self.assertIn('Regulax AI', content)
        self.assertIn('Quality records,', content)
        self.assertIn('signed with', content)
        self.assertIn('confidence.', content)
        self.assertIn('Every entry is attributable, legible and contemporaneous.', content)
        self.assertIn('Electronic signatures on every approval', content)
        self.assertIn('A complete, time-stamped audit trail', content)
        self.assertIn('Role-based access, individual accounts', content)
        self.assertIn('Sign in', content)
        self.assertIn('Use your individual account. Accounts are never shared.', content)
        self.assertIn('firstname.lastname', content)
        self.assertIn('Remember username', content)
        self.assertIn('Forgot password?', content)
        self.assertIn('Two-step verification is required.', content)
        self.assertIn('Request access', content)

    def test_login_successful_redirects_to_dashboard(self):
        response = self.client.post(
            reverse('login'),
            {'username': self.username, 'password': self.password},
            follow=False
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('dashboard'))

    def test_login_remember_username_sets_cookie(self):
        response = self.client.post(
            reverse('login'),
            {
                'username': self.username,
                'password': self.password,
                'remember_username': 'on'
            },
            follow=False
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('remembered_username', response.cookies)
        self.assertEqual(response.cookies['remembered_username'].value, self.username)

    def test_login_invalid_credentials_shows_error(self):
        response = self.client.post(
            reverse('login'),
            {'username': self.username, 'password': 'WrongPassword!'},
            follow=False
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Invalid username or password', content)

    def test_authenticated_user_redirected_away_from_login(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('dashboard'))

    def test_logout_redirects_to_login(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('login'))


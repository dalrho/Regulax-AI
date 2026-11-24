from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.register.models import UserProfile
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import ensure_personal_workspace


class WorkspaceOnboardingTests(TestCase):
    def make_user(self, username, email=None, role=None):
        user = User.objects.create_user(username=username, email=email or f'{username}@example.test', password='StrongPassword123!')
        UserProfile.objects.create(user=user, full_name=username.title(), company_name=f'{username} Company')
        return user

    def test_registration_creates_one_workspace_owner_membership(self):
        response = self.client.post(reverse('register'), {
            'full_name': 'Workspace Owner',
            'work_email': 'owner@company.test',
            'company_name': 'Company One',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
            'compliance_ack': 'on',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='owner@company.test')
        self.assertEqual(Workspace.objects.filter(memberships__user=user).count(), 1)
        membership = Membership.objects.get(user=user)
        self.assertEqual(membership.role, Membership.Role.OWNER)
        self.assertEqual(membership.workspace.company_name, 'Company One')

    def test_legacy_onboarding_is_idempotent_and_private(self):
        first_user = self.make_user('legacy-one')
        second_user = self.make_user('legacy-two')
        first_membership = ensure_personal_workspace(first_user)
        again = ensure_personal_workspace(first_user)
        second_membership = ensure_personal_workspace(second_user)

        self.assertEqual(first_membership.pk, again.pk)
        self.assertNotEqual(first_membership.workspace_id, second_membership.workspace_id)
        self.assertEqual(Workspace.objects.filter(memberships__user=first_user).count(), 1)

    def test_backfill_command_can_be_repeated(self):
        user = self.make_user('backfill-user')
        call_command('backfill_workspaces', verbosity=0)
        first_workspace_id = Membership.objects.get(user=user).workspace_id
        call_command('backfill_workspaces', verbosity=0)
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)
        self.assertEqual(Membership.objects.get(user=user).workspace_id, first_workspace_id)


class WorkspaceAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='StrongPassword123!')
        self.reviewer = User.objects.create_user(username='reviewer', password='StrongPassword123!')
        self.workspace = Workspace.objects.create(company_name='Owner Workspace')
        self.owner_membership = Membership.objects.create(user=self.owner, workspace=self.workspace, role=Membership.Role.OWNER)
        self.reviewer_membership = Membership.objects.create(user=self.reviewer, workspace=self.workspace, role=Membership.Role.REVIEWER)

    def test_owner_can_save_workspace_settings(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('workspace_settings'), {
            'company_name': 'Updated Company',
            'site': 'Plant 2',
            'product_category': 'Drug product',
            'jurisdiction': 'United States',
        })
        self.assertRedirects(response, reverse('workspace_settings'))
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.company_name, 'Updated Company')
        self.assertEqual(self.workspace.jurisdiction, 'United States')

    def test_reviewer_cannot_view_or_change_owner_settings(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse('workspace_settings'))
        self.assertEqual(response.status_code, 403)
        self.client.post(reverse('workspace_settings'), {
            'company_name': 'Unauthorized Change',
            'site': '',
            'product_category': '',
            'jurisdiction': '',
        })
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.company_name, 'Owner Workspace')

    def test_membership_id_from_another_workspace_is_rejected(self):
        other_workspace = Workspace.objects.create(company_name='Other Workspace')
        foreign_member = User.objects.create_user(username='foreign', password='StrongPassword123!')
        foreign_membership = Membership.objects.create(
            user=foreign_member,
            workspace=other_workspace,
            role=Membership.Role.REVIEWER,
        )
        self.client.force_login(self.owner)
        response = self.client.post(reverse('workspace_member_remove', args=[foreign_membership.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Membership.objects.filter(pk=foreign_membership.pk).exists())

    def test_last_owner_cannot_be_removed_or_downgraded(self):
        self.reviewer_membership.delete()
        self.client.force_login(self.owner)

        remove_response = self.client.post(reverse('workspace_member_remove', args=[self.owner_membership.pk]))
        self.assertRedirects(remove_response, reverse('workspace_members'))
        self.assertTrue(Membership.objects.filter(pk=self.owner_membership.pk).exists())

        role_response = self.client.post(
            reverse('workspace_member_role', args=[self.owner_membership.pk]),
            {'role': Membership.Role.REVIEWER},
        )
        self.assertRedirects(role_response, reverse('workspace_members'))
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, Membership.Role.OWNER)

    def test_dashboard_onboards_existing_user_and_resolves_membership(self):
        existing_user = User.objects.create_user(username='existing', password='StrongPassword123!')
        UserProfile.objects.create(user=existing_user, full_name='Existing User', company_name='Existing Company')
        self.client.force_login(existing_user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        membership = Membership.objects.get(user=existing_user)
        self.assertEqual(membership.role, Membership.Role.OWNER)
        self.assertEqual(response.context['workspace'], membership.workspace)
        self.assertContains(response, 'Existing Company workspace')

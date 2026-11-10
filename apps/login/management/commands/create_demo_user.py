from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'Creates a demo user for testing the login screen'

    def handle(self, *args, **options):
        username = 'jane.doe'
        password = 'Password123!'
        email = 'jane.doe@example.com'

        user, created = User.objects.get_or_create(username=username, defaults={'email': email})
        user.set_password(password)
        user.first_name = 'Jane'
        user.last_name = 'Doe'
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created demo user '{username}' with password '{password}'"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated demo user '{username}' password to '{password}'"))


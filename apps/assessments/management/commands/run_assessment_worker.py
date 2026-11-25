import time

from django.core.management.base import BaseCommand, CommandError

from apps.assessments.execution import DEFAULT_LEASE_SECONDS, worker_once
from apps.assessments.providers import DeterministicProvider


class Command(BaseCommand):
    help = 'Claim durable assessment jobs and run their pending requirements.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Run at most one claimed job attempt, then exit.')
        parser.add_argument('--poll-interval', type=float, default=2.0)
        parser.add_argument('--lease-seconds', type=int, default=DEFAULT_LEASE_SECONDS)
        parser.add_argument(
            '--provider', choices=('configured', 'deterministic'), default='configured',
            help='Use the provider pinned when the assessment was queued, or the labeled development double.',
        )

    def handle(self, *args, **options):
        provider = DeterministicProvider() if options['provider'] == 'deterministic' else None
        try:
            while True:
                outcome = worker_once(provider=provider, lease_seconds=options['lease_seconds'])
                if outcome is None:
                    if options['once']:
                        self.stdout.write('No due assessment job was available.')
                        return
                    time.sleep(max(0.25, options['poll_interval']))
                    continue
                self.stdout.write(f'Assessment worker attempt ended with state: {outcome}.')
                if options['once']:
                    return
        except KeyboardInterrupt:
            self.stdout.write('Assessment worker stopped.')
        except Exception as error:
            raise CommandError(f'{type(error).__name__}: {error}') from error

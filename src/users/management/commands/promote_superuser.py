"""Grant superuser rights to an existing account.

Instance-wide settings (Settings > Metadata, the image cache on Advanced) are
superuser-only, but Floppy's normal setup never flags anyone, so the owner of a
fresh install has no way in. Shell access is already root-equivalent here -
``createsuperuser`` lives in the same place - so proving it by running this
command grants no privilege the caller did not already have.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Promote an existing user to superuser."""

    help = "Grant superuser rights to an existing account."

    def add_arguments(self, parser):
        """Register the command's arguments."""
        parser.add_argument(
            "username",
            nargs="?",
            help="Account to promote. Omit with --list to see the choices.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List accounts and their current superuser status, then exit.",
        )
        parser.add_argument(
            "--revoke",
            action="store_true",
            help="Remove superuser rights instead of granting them.",
        )

    def handle(self, *args, **options):
        """Promote, revoke, or list, depending on the arguments."""
        user_model = get_user_model()

        if options["list"]:
            self._list_users(user_model)
            return

        username = options["username"]
        if not username:
            msg = "Give a username, or pass --list to see the choices."
            raise CommandError(msg)

        try:
            user = user_model.objects.get(username=username)
        except user_model.DoesNotExist as error:
            msg = f"No account named {username!r}. Run with --list to see them."
            raise CommandError(msg) from error

        if options["revoke"]:
            self._revoke(user_model, user)
            return

        if user.is_superuser and user.is_staff:
            self.stdout.write(f"{user.username} is already a superuser.")
            return

        user.is_superuser = True
        user.is_staff = True
        user.save(update_fields=["is_superuser", "is_staff"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{user.username} is now a superuser. "
                "Sign out and back in for the change to show up.",
            ),
        )

    def _revoke(self, user_model, user):
        """Drop superuser rights, refusing to remove the last one."""
        if not user.is_superuser:
            self.stdout.write(f"{user.username} is not a superuser.")
            return

        remaining = user_model.objects.filter(is_superuser=True).exclude(pk=user.pk)
        if not remaining.exists():
            msg = (
                f"{user.username} is the only superuser. Promote another account "
                "first, or the instance would have no one who can change "
                "instance-wide settings."
            )
            raise CommandError(msg)

        user.is_superuser = False
        user.is_staff = False
        user.save(update_fields=["is_superuser", "is_staff"])
        self.stdout.write(
            self.style.SUCCESS(f"{user.username} is no longer a superuser.")
        )

    def _list_users(self, user_model):
        """Print every account with its superuser status."""
        users = user_model.objects.order_by("id").values_list(
            "username",
            "is_superuser",
        )
        if not users:
            self.stdout.write("No accounts yet.")
            return

        superusers = [name for name, is_super in users if is_super]
        for name, is_super in users:
            marker = "superuser" if is_super else "-"
            self.stdout.write(f"{marker:>10}  {name}")

        self.stdout.write("")
        if superusers:
            self.stdout.write(
                f"{len(superusers)} superuser(s): {', '.join(superusers)}"
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No superusers. Nobody can change instance-wide settings until "
                    "you promote an account.",
                ),
            )

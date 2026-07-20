"""Vérifie que le journal d'audit n'a pas été altéré.

    python manage.py verifier_audit

Recalcule la chaîne d'empreintes de bout en bout. Si une entrée a été modifiée
ou supprimée directement en base, la chaîne est rompue et la commande signale
la première entrée en cause.
"""

from django.core.management.base import BaseCommand

from parcelles.audit import verifier_integrite


class Command(BaseCommand):
    help = "Vérifie l'intégrité (chaînage cryptographique) du journal d'audit."

    def handle(self, *args, **options):
        ok, n, rupture = verifier_integrite()
        if ok:
            self.stdout.write(self.style.SUCCESS(
                f"Journal intègre : {n} entrée(s) vérifiée(s), chaîne complète."
            ))
            return
        self.stdout.write(self.style.ERROR(
            f"ALTÉRATION DÉTECTÉE après {n} entrée(s) valide(s)."
        ))
        if rupture:
            self.stdout.write(
                f"  Première entrée suspecte : #{rupture.pk} — "
                f"{rupture.created_at:%d/%m/%Y %H:%M} — {rupture.get_action_display()}"
            )
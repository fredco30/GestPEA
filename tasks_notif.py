"""
À AJOUTER dans app/tasks.py — en bas du fichier existant
----------------------------------------------------------
Tâches Celery pour les notifications et le digest hebdomadaire.
"""

from celery import shared_task
import logging
logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def notifier_alerte_task(self, alerte_id: int):
    """
    Envoie une alerte sur tous les canaux configurés (email, Telegram, webhook).
    Appelée automatiquement après scorer_alerte_task.

    Ajouter dans la chaîne de scorer_alerte_task :
        from app.tasks import notifier_alerte_task
        notifier_alerte_task.delay(alerte_id)
    """
    from app.services.notifications import notifier_alerte
    try:
        resultats = notifier_alerte(alerte_id)
        return {'status': 'ok', 'alerte_id': alerte_id, 'canaux': resultats}
    except Exception as exc:
        logger.error(f"[Task] notifier_alerte {alerte_id} — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=1)
def digest_hebdomadaire_task(self):
    """
    Génère et envoie le digest hebdomadaire (vendredi soir 19h00).
    Planifié dans CELERY_BEAT_SCHEDULE.
    """
    from app.services.scoring_llm import generer_digest_hebdomadaire
    from app.services.notifications import notifier_digest
    try:
        texte     = generer_digest_hebdomadaire()
        resultats = notifier_digest(texte)
        logger.info(f"[Task] digest_hebdomadaire envoyé — canaux : {resultats}")
        return {'status': 'ok', 'canaux': resultats}
    except Exception as exc:
        logger.error(f"[Task] digest_hebdomadaire — erreur : {exc}", exc_info=True)
        raise self.retry(exc=exc)

"""
scheduler.py — APScheduler para automações CRM.
Verifica follow-ups pendentes e executa envios automáticos.
"""

import time
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

import database
import events

scheduler = BackgroundScheduler()


def _parse_time(time_str: str) -> tuple[int, int]:
    """Converte string 'HH:MM' para (hora, minuto)."""
    try:
        parts = time_str.strip().split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 8, 0


def _is_within_send_window(start_str: str, end_str: str) -> bool:
    """Verifica se o horário atual está dentro da janela de envio."""
    now = datetime.utcnow()
    current_minutes = now.hour * 60 + now.minute
    start_h, start_m = _parse_time(start_str)
    end_h, end_m = _parse_time(end_str)
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes <= end_minutes
    else:
        return current_minutes >= start_minutes or current_minutes <= end_minutes


def _next_window_start(start_str: str) -> datetime:
    """Retorna o próximo horário de início da janela de envio."""
    now = datetime.utcnow()
    start_h, start_m = _parse_time(start_str)
    today_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    if today_start > now:
        return today_start
    return today_start + timedelta(days=1)


def _replace_variables(template: str, contact: dict) -> str:
    """Substitui variáveis na mensagem: {{nome}}, {{numero}}."""
    result = template
    result = result.replace("{{nome}}", contact.get("name") or "cliente")
    result = result.replace("{{numero}}", contact.get("phone") or "")
    return result


def check_follow_ups():
    """Job principal: verifica e executa follow-ups pendentes."""
    try:
        pending = database.get_pending_follow_ups()
        if not pending:
            return

        now = datetime.utcnow()
        now_iso = now.isoformat()

        for fu in pending:
            try:
                fu_id = fu["id"]
                contact_id = fu["contact_id"]
                automation_id = fu["automation_id"]
                scheduled_for = fu["scheduled_for"]
                message_template = fu.get("message_template", "")
                use_meta_template = fu.get("use_meta_template", 0)
                meta_template_name = fu.get("meta_template_name", "")
                meta_template_language = fu.get("meta_template_language", "pt_BR")
                send_window_start = fu.get("send_window_start", "08:00")
                send_window_end = fu.get("send_window_end", "18:00")
                action_tag_add = fu.get("action_tag_add", "")
                action_tag_remove = fu.get("action_tag_remove", "")
                action_status_change = fu.get("action_status_change", "")

                contact = database.get_contact_by_id(contact_id)
                if not contact:
                    database.update_follow_up_status(fu_id, "erro", error_message="Contato nao encontrado")
                    continue

                # Verificar se o contato interagiu novamente (cancelamento automatico)
                last_interaction = contact.get("last_interaction_at") or ""
                if last_interaction:
                    try:
                        li_dt = datetime.fromisoformat(last_interaction.replace("Z", "+00:00").replace("+00:00", ""))
                        fu_dt = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00").replace("+00:00", ""))
                        if li_dt > fu_dt:
                            database.update_follow_up_status(fu_id, "cancelado", error_message="Contato interagiu novamente")
                            continue
                    except Exception:
                        pass

                # Verificar janela de envio
                if not _is_within_send_window(send_window_start, send_window_end):
                    next_start = _next_window_start(send_window_start)
                    database.update_follow_up_scheduled(fu_id, next_start.isoformat())
                    continue

                # Preparar mensagem
                message = _replace_variables(message_template, contact)
                phone = contact.get("phone", "")

                # Enviar
                if use_meta_template and meta_template_name:
                    from meta import send_template
                    body_params = [contact.get("name", "cliente")]
                    result = send_template(phone, meta_template_name, meta_template_language, body_params)
                else:
                    from meta import send_text
                    result = send_text(phone, message)

                if result.get("ok"):
                    database.update_follow_up_status(fu_id, "enviado", sent_at=now_iso, message_sent=message)
                    database.create_crm_log(
                        automation_id=automation_id,
                        contact_id=contact_id,
                        action="send",
                        message=message,
                        result="enviado",
                    )
                    # Executar ações
                    if action_tag_add:
                        tag = database.get_tag(int(action_tag_add)) if action_tag_add.isdigit() else None
                        if tag:
                            database.add_tag_to_contact(contact_id, tag["id"])
                    if action_tag_remove:
                        try:
                            database.remove_tag_from_contact(contact_id, int(action_tag_remove))
                        except Exception:
                            pass
                    if action_status_change:
                        database.update_contact(contact_id, crm_status=action_status_change)

                    events.emit("crm_follow_up_sent", {
                        "follow_up_id": fu_id,
                        "contact_id": contact_id,
                        "automation_id": automation_id,
                    })
                    print(f"[crm] follow-up {fu_id} enviado para {phone}")
                else:
                    error_msg = result.get("error", "Erro desconhecido")
                    database.update_follow_up_status(fu_id, "erro", error_message=error_msg)
                    database.create_crm_log(
                        automation_id=automation_id,
                        contact_id=contact_id,
                        action="send_error",
                        message=message,
                        result="erro",
                        error=error_msg,
                    )
                    print(f"[crm] follow-up {fu_id} ERRO: {error_msg}")

            except Exception as e:
                print(f"[crm] ERRO ao processar follow-up {fu.get('id', '?')}: {e}")

    except Exception as e:
        print(f"[crm] ERRO no check_follow_ups: {e}")


def start_scheduler():
    """Inicia o scheduler APScheduler."""
    scheduler.add_job(check_follow_ups, "interval", minutes=1, id="check_follow_ups")
    scheduler.start()
    print("[crm] scheduler iniciado (check_follow_ups a cada 1 min)")


def stop_scheduler():
    """Para o scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[crm] scheduler parado")

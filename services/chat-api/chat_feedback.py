"""Read bridge-authored status events, never infer delivery from message text."""
import re

STATES={'SUCCESS':'sent','PENDING':'pending','FAIL_RETRIABLE':'failed','FAIL_PERMANENT':'failed'}
REASONS={'m.event_not_handled','com.beeper.unsupported_event','com.beeper.undecryptable_event',
         'm.event_too_old','m.foreign_network_error','m.no_permission','m.bridge_unavailable'}


def status_updates(api,events,platform):
    bridge=api.BRIDGES[platform];updates=[]
    domain=api.MATRIX_USER_ID.split(':',1)[1]
    for e in events:
        if isinstance(e,dict) and platform=='imo' and e.get('type')=='com.matrix.imo.status' and e.get('sender')==bridge['bot_mxid']:
            c=e.get('content',{});c=c if isinstance(c,dict) else {}
            target=c.get('event_id');stamp=e.get('origin_server_ts')
            state={'accepted_by_imo':'sent','delivered':'delivered','seen':'delivered','error':'failed','unknown':'pending'}.get(c.get('status'))
            if isinstance(target,str) and target.startswith('$') and len(target)<=256 and not any(ord(ch)<33 for ch in target) and type(stamp)is int and state:
                updates.append(dict(event_id=target,state=state,reason='m.foreign_network_error' if state=='failed' else '',timestamp=stamp))
            continue
        if not isinstance(e,dict) or e.get('type')!='com.beeper.message_send_status':continue
        sender=e.get('sender','')
        # In unencrypted portals the bridge may use its appservice ghost intent.
        trusted=isinstance(sender,str) and (sender==bridge['bot_mxid'] or sender.startswith(bridge['ghost_prefix']) and sender.endswith(':'+domain))
        if not trusted:continue
        c=e.get('content',{});c=c if isinstance(c,dict) else {}
        rel=c.get('m.relates_to',{});rel=rel if isinstance(rel,dict) else {}
        target=rel.get('event_id');state=STATES.get(c.get('status')) if isinstance(c.get('status'),str) else None
        stamp=e.get('origin_server_ts')
        if not isinstance(target,str) or not target.startswith('$') or len(target)>256 or any(ord(ch)<33 for ch in target) or not state or type(stamp) is not int:continue
        if state=='sent' and isinstance(c.get('delivered_to_users'),list) and len(c['delivered_to_users'])>0:state='delivered'
        reason=c.get('reason')
        updates.append(dict(event_id=target,state=state,reason=reason if isinstance(reason,str) and reason in REASONS else '',timestamp=stamp))
    # Latest first/oldest first pages must yield the same result.
    return sorted(updates,key=lambda u:(u['timestamp'],{'pending':0,'failed':1,'sent':2,'delivered':3}[u['state']]))

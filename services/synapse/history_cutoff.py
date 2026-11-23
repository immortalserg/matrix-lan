"""Reject pre-cutoff message imports only in the selected local bridge rooms."""
class HistoryCutoff:
    MESSAGE_TYPES = frozenset({
        'm.room.message', 'm.room.encrypted', 'm.sticker', 'm.poll.start',
        'org.matrix.msc3381.poll.start', 'org.matrix.msc1767.message',
    })

    @staticmethod
    def parse_config(config):
        cutoff = int(config['cutoff_ms'])
        platforms = set(config.get('platforms', []))
        if cutoff <= 0 or not platforms or not platforms <= {'whatsapp', 'telegram'}:
            raise ValueError('History cutoff must explicitly select WhatsApp/Telegram')
        return dict(config, cutoff_ms=cutoff, platforms=sorted(platforms))

    def __init__(self, config, api):
        self.config = self.parse_config(config)
        self.cutoff = self.config['cutoff_ms']
        self.platforms = frozenset(self.config['platforms'])
        self.rooms = frozenset(self.config.get('room_ids', []))
        self.server = self.config['server_name']
        api.register_third_party_rules_callbacks(check_event_allowed=self.check_event_allowed)

    async def check_event_allowed(self, event, state_events):
        if event.type not in self.MESSAGE_TYPES or event.is_state():
            return True, None
        if event.sender.partition(':')[2] != self.server:
            return True, None
        timestamp = event.origin_server_ts
        if not isinstance(timestamp, int) or timestamp >= self.cutoff:
            return True, None
        if event.room_id in self.rooms:
            return False, None
        for (kind, _), state in state_events.items():
            if kind in ('m.bridge', 'uk.half-shot.bridge'):
                protocol = state.content.get('protocol', {})
                if isinstance(protocol, dict) and protocol.get('id') in self.platforms:
                    return False, None
        return True, None

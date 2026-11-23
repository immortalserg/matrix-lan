# Сторонние компоненты и исходники

- Element Web v1.12.33. Базовый образ `vectorim/element-web:v1.12.33`. Дополнительные файлы и изменения сборки находятся в `services/element/overlay/`. Исходная версия и лицензии доступны в [Element Web](https://github.com/element-hq/element-web/tree/v1.12.33).
- Synapse v1.164.1, PostgreSQL 16 и Caddy 2 загружаются из указанных в шаблоне образов. [Synapse](https://github.com/element-hq/synapse), [PostgreSQL](https://www.postgresql.org/about/licence/), [Caddy](https://github.com/caddyserver/caddy).
- Facebook Messenger собирается из vendored mautrix-meta на основе `d1c0f61206494e2709099e8b737a6ceb2ff6a083`, с доработкой выбора MFA. Исходники и лицензии в `services/facebook/source/`. Instagram использует образ upstream. [mautrix-meta](https://github.com/mautrix/meta).
- WhatsApp, Telegram, X и LinkedIn загружаются из репозиториев Mautrix. [WhatsApp](https://github.com/mautrix/whatsapp), [Telegram](https://github.com/mautrix/telegram), [X](https://github.com/mautrix/twitter), [LinkedIn](https://github.com/mautrix/linkedin).
- TikTok и Matrimail с фильтром Gmail собираются из исходников под `services/tiktok/source/` и `services/email/source/`. Там же сохранены лицензии. [Matrix TikTok](https://github.com/httpjamesm/matrix-tiktok), [Matrimail](https://github.com/Leicas/matrimail), [go-imap](https://github.com/emersion/go-imap).
- Native WeChat поставляется как закреплённый образ стороннего проекта. Matrix-адаптер находится в `services/wechat/`. [Agent WeChat](https://github.com/thisnick/agent-wechat).
- Chromium и Playwright загружаются через официальный образ Microsoft. [Playwright](https://github.com/microsoft/playwright). Иконки идентифицируют соответствующие продукты, товарные знаки принадлежат их владельцам.

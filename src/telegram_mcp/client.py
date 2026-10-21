"""Thin wrapper around the Telethon client.

Two jobs. The first is a known Telethon quirk: ``get_entity`` by id only
succeeds for entities already present in the session cache. The first miss warms
the cache via ``get_dialogs()`` and retries. The dialogs themselves are never
returned anywhere — only allowlisted chats ever leave this process.

The second is keeping raw Telegram requests out of the handlers: the reaction,
dialog and poll calls below build TL objects here, so the handlers keep talking to a
plain interface that a fake can stand in for.
"""
from __future__ import annotations


class CachedClient:
    def __init__(self, inner):
        self._inner = inner
        self._warmed = False
        # Custom emoji documents never change, so their emoji are fetched once per process.
        self._custom_emoji: dict[int, str] = {}

    async def get_entity(self, chat_id):
        try:
            return await self._inner.get_entity(chat_id)
        except Exception:
            if self._warmed:
                raise
            self._warmed = True
            await self._inner.get_dialogs()
            return await self._inner.get_entity(chat_id)

    async def get_reaction_peers(self, entity, message_ids):
        """The users and chats behind the reactions of these messages, in one request.

        Names come straight from Telegram's answer instead of get_entity: members
        of a big chat arrive as "min" users, which Telethon cannot resolve by id.
        """
        from telethon.tl.functions.messages import GetMessagesReactionsRequest  # noqa: PLC0415

        updates = await self._inner(GetMessagesReactionsRequest(peer=entity, id=list(message_ids)))
        return [*(getattr(updates, "users", None) or []), *(getattr(updates, "chats", None) or [])]

    async def get_messages(self, entity, **kwargs):
        return await self._inner.get_messages(entity, **kwargs)

    async def get_peer_dialogs(self, entities):
        """The dialogs of these chats — unread count and read position — in one request.

        messages.getPeerDialogs only reports where the inbox has been read up to:
        it marks nothing as read. Returns Telegram's dialogs as they are; a chat
        Telegram keeps no dialog for (a channel that was never joined) is missing.
        """
        from telethon.tl.functions.messages import GetPeerDialogsRequest  # noqa: PLC0415

        answer = await self._inner(GetPeerDialogsRequest(peers=list(entities)))
        return list(getattr(answer, "dialogs", None) or [])

    async def get_custom_emoji(self, document_ids) -> dict[int, str]:
        """The emoji each custom emoji stands for, keyed by its document id."""
        wanted = list(dict.fromkeys(document_ids))
        missing = [i for i in wanted if i not in self._custom_emoji]
        if missing:
            from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest  # noqa: PLC0415

            for document in await self._inner(GetCustomEmojiDocumentsRequest(document_id=missing)):
                for attribute in getattr(document, "attributes", None) or []:
                    alt = getattr(attribute, "alt", None)
                    if type(attribute).__name__ == "DocumentAttributeCustomEmoji" and alt:
                        self._custom_emoji[document.id] = alt
        return {i: self._custom_emoji[i] for i in wanted if i in self._custom_emoji}

    async def get_reactions_list(self, entity, message_id, reaction, limit, offset):
        """One page of who reacted to a message; ``reaction`` narrows it to one emoji.

        A string of ASCII digits is a custom emoji document id, anything else a
        standard emoji. Returns Telegram's messages.MessageReactionsList as is.
        """
        from telethon.tl.functions.messages import GetMessageReactionsListRequest  # noqa: PLC0415
        from telethon.tl.types import ReactionCustomEmoji, ReactionEmoji  # noqa: PLC0415

        if reaction is None:
            wanted = None
        elif reaction.isascii() and reaction.isdigit():
            document_id = int(reaction)
            if document_id >= 2**63:
                raise ValueError(f"{reaction!r} is too large for a custom_emoji_id.")
            wanted = ReactionCustomEmoji(document_id=document_id)
        else:
            wanted = ReactionEmoji(emoticon=reaction)
        return await self._inner(
            GetMessageReactionsListRequest(
                peer=entity, id=message_id, limit=limit, reaction=wanted, offset=offset
            )
        )

    async def get_poll_votes(self, entity, message_id, option, limit, offset):
        """One page of who voted in a poll; ``option`` (the raw option bytes) narrows it to one.

        messages.getPollVotes only reads the votes: it casts none. Returns Telegram's
        messages.VotesList as is.
        """
        from telethon.errors import PollVoteRequiredError  # noqa: PLC0415
        from telethon.tl.functions.messages import GetPollVotesRequest  # noqa: PLC0415

        try:
            return await self._inner(
                GetPollVotesRequest(peer=entity, id=message_id, limit=limit, option=option, offset=offset)
            )
        except PollVoteRequiredError:
            raise ValueError(
                "Telegram shows who voted in this poll only to those who voted in it — and this "
                "server never votes."
            ) from None

    async def download_media(self, message, file, progress_callback=None):
        return await self._inner.download_media(
            message, file=file, progress_callback=progress_callback
        )

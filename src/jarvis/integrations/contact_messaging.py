"""Explicit approved message effects. No polling, retries or delivery guesses."""
from email.message import EmailMessage
from email.utils import make_msgid
import hashlib
import re
import smtplib
import ssl
from urllib.parse import urlencode
import webbrowser

from jarvis.application.contacts import email_address, whatsapp_number


class MessageRejectedError(RuntimeError):
    pass


class ContactMessagingAdapter:
    def __init__(self, *, opener=None, smtp_factory=None):
        self.opener = opener or webbrowser.open
        self.smtp_factory = smtp_factory

    @staticmethod
    def validate_smtp(row):
        if set(row) != {'host', 'port', 'username', 'password', 'sender'}:
            raise ValueError('Configuração de e-mail inválida.')
        if (not isinstance(row['host'], str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', row['host'])
                or type(row['port']) is not int or row['port'] not in {465, 587}
                or not isinstance(row['username'], str) or not 1 <= len(row['username']) <= 254
                or any(ord(c) < 32 for c in row['username'])
                or not isinstance(row['password'], str) or not 1 <= len(row['password']) <= 512
                or not isinstance(row['sender'], str) or not email_address(row['sender'])):
            raise ValueError('Use servidor SMTP com TLS, porta 465 ou 587 e uma conta de envio válida.')

    def send(self, draft, smtp):
        if draft['channel'] == 'whatsapp':
            phone = whatsapp_number(draft['recipient'])
            if not phone:
                raise MessageRejectedError('Destinatário WhatsApp ausente.')
            # Official click-to-chat: opens a draft, never claims a sent message.
            # https://faq.whatsapp.com/5913398998672934
            url = 'https://wa.me/' + phone[1:] + '?' + urlencode({'text': draft['body']})
            if not self.opener(url, new=2):
                raise MessageRejectedError('Não foi possível abrir o WhatsApp.')
            return {'state': 'handed_off', 'receipt': '',
                    'message': 'Abertura do WhatsApp solicitada com a mensagem pronta. Conclua o envio no aplicativo; o JARVIS não confirma entrega.'}
        if draft['channel'] != 'email' or not smtp:
            raise MessageRejectedError('Conta de envio por e-mail não configurada.')
        self.validate_smtp(smtp)
        recipient = email_address(draft['recipient'])
        if not recipient:
            raise MessageRejectedError('Destinatário de e-mail ausente.')
        message = EmailMessage()
        message['From'] = smtp['sender']
        message['To'] = recipient
        message['Subject'] = draft['subject']
        message['Message-ID'] = make_msgid(domain='jarvis.local')
        message.set_content(draft['body'])
        try:
            factory = self.smtp_factory or (smtplib.SMTP_SSL if smtp['port'] == 465 else smtplib.SMTP)
            options = {'timeout': 20}
            if smtp['port'] == 465:
                options['context'] = ssl.create_default_context()
            with factory(smtp['host'], smtp['port'], **options) as server:
                if smtp['port'] == 587:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(smtp['username'], smtp['password'])
                refused = server.send_message(message, from_addr=smtp['sender'], to_addrs=[recipient])
                if refused:
                    raise MessageRejectedError('O servidor recusou o destinatário.')
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            raise MessageRejectedError('O servidor recusou o envio. Confira a conta e o destinatário.') from None
        except MessageRejectedError:
            raise
        except Exception:
            raise RuntimeError('Resultado do envio incerto; não haverá repetição automática.') from None
        return {'state': 'accepted', 'receipt': hashlib.sha256(str(message['Message-ID']).encode()).hexdigest(),
                'message': 'Mensagem aceita pelo servidor de e-mail. Isso não confirma entrega ou leitura pelo destinatário.'}

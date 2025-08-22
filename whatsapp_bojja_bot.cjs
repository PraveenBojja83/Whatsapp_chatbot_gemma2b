// node whatsapp_bojja_bot.cjs

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore
} = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const axios = require('axios');
const P = require('pino');
const fs = require('fs');
const qrcode = require('qrcode-terminal');

const logger = P({ level: 'silent' });

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState('auth_info1');
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    printQRInTerminal: false,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    logger
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) qrcode.generate(qr, { small: true });

    if (connection === 'close') {
      const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      console.log("❌ Connection closed. Reconnecting...", shouldReconnect);
      if (shouldReconnect) startSock();
    } else if (connection === 'open') {
      console.log("✅ WhatsApp bot connected!");
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    const msg = messages[0];
    if (!msg.message || msg.key.fromMe) return;

    const sender = msg.key.remoteJid;
    const textMsg = msg.message?.conversation || msg.message?.extendedTextMessage?.text;
    const normalizedText = (textMsg || "").toLowerCase().trim();
    const cleanedText = normalizedText.replace(/[^a-z0-9 ]/gi, '');

    if (!textMsg) return;

    const now = new Date();
    const hour = now.getHours();
    let timeGreeting = "Hello";
    if (hour >= 5 && hour < 12) timeGreeting = "🌅 Good Morning";
    else if (hour >= 12 && hour < 17) timeGreeting = "☀️ Good Afternoon";
    else timeGreeting = "🌇 Good Evening";

    // Unified welcome message
    const welcomeText = `${timeGreeting}! 👋\nWelcome to Bojja's Resort Bot 🏨🤖\nHow can I assist you today?`;

    // Greetings and room statements both trigger welcome message
    const greetingWords = ["resort bot", "hi", "hii", "hello", "hey"];
    const roomMatch = normalizedText.match(/(?:i am|i'm|am|i\s*in|in|from|room)\s+(?:in\s+)?(\d{2,4})/i);

    if (
      msg.messageStubType === 28 ||
      msg.messageStubType === 'PEER_DEVICE_ADDED' ||
      greetingWords.includes(normalizedText) ||
      roomMatch
    ) {
      console.log("👋 Sending unified welcome message");
      await sock.sendMessage(sender, { text: welcomeText });
      return;
    }

    const exitWords = ["bye", "byee", "thankyou", "thank you", "thanks", "ok","ok thank you","ok thanks","exit", "quit"];
    if (exitWords.includes(normalizedText)) {
      const bye = "👋 Thank you for chatting with Bojja's Resort Bot. Have a great stay!";
      console.log(`✅ Answer: ${bye}`);
      await sock.sendMessage(sender, { text: bye });
      return;
    }

    try {
      const response = await axios.post('http://127.0.0.1:5000/query', {
        question: textMsg,
        phone: sender
      });

      const replyText = response.data.answer || "🤖 No reply from bot.";
      console.log(`✅ Answer: ${replyText}`);
      await sock.sendMessage(sender, { text: replyText });
    } catch (err) {
      console.error("⚠️ Flask error:", err.message);
      const fallback = "❌ Bot is facing issues. Please try again later.";
      await sock.sendMessage(sender, { text: fallback });
    }
  });
}

console.log("🚀 Starting WhatsApp bot...");
startSock();

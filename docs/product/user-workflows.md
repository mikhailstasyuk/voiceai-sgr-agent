# User Workflows

## Start Conversation
1. Open frontend app.
2. Click mic button.
3. Wait for connected/ready status.
4. Speak into microphone.

Expected result:
- User transcript appears.
- Assistant begins speaking with streamed audio.

## Interrupt Assistant (Barge-In)
1. While assistant is speaking, user starts speaking again.

Expected result:
- Ongoing assistant output is interrupted quickly.
- New user utterance becomes active turn input.

## Stop Conversation
1. Click mic button again (stop).

Expected result:
- Session closes cleanly.
- UI returns to idle state.

## Error Recovery
1. If status enters error, user restarts session.

Expected result:
- New clean session can be started without page reload in normal cases.


export interface TtsEngine {
  readonly name: string
  speak(text: string): Promise<void>
  stop(): void
  isSpeaking(): boolean
}

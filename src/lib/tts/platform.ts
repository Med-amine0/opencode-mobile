import * as SpeechModule from "expo-speech"
import type { TtsEngine } from "./types"

export function createPlatformTts(onDone?: () => void): TtsEngine {
  return {
    name: "Platform (Default)",
    speak: async (text: string) => {
      return new Promise<void>((resolve) => {
        SpeechModule.speak(text, {
          rate: 0.9,
          pitch: 1.0,
          onDone: () => {
            resolve()
            onDone?.()
          },
          onError: () => {
            resolve()
            onDone?.()
          },
        })
      })
    },
    stop: () => {
      SpeechModule.stop()
    },
    isSpeaking: () => false,
  }
}

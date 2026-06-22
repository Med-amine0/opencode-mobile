import { useState, useCallback, useRef, useEffect } from "react"
import type { TtsEngine } from "./types"
import { createPlatformTts } from "./platform"

export type TtsEngineName = "platform"

const engines: Record<TtsEngineName, () => TtsEngine> = {
  platform: () => createPlatformTts(),
}

export function useTts() {
  const [muted, setMuted] = useState(true)
  const [speaking, setSpeaking] = useState(false)
  const engineRef = useRef<TtsEngine>(createPlatformTts())
  const queueRef = useRef<string[]>([])
  const busyRef = useRef(false)
  const genRef = useRef(0)

  const processQueue = useCallback(() => {
    if (busyRef.current || queueRef.current.length === 0) return
    const gen = genRef.current
    busyRef.current = true
    const text = queueRef.current.shift()!
    setSpeaking(true)
    engineRef.current.speak(text).then(() => {
      if (gen !== genRef.current) return // stale
      busyRef.current = false
      if (queueRef.current.length === 0) {
        setSpeaking(false)
      } else {
        processQueue()
      }
    }).catch(() => {
      if (gen !== genRef.current) return
      busyRef.current = false
      setSpeaking(false)
    })
  }, [])

  const toggleMute = useCallback(() => {
    if (!muted) {
      genRef.current++
      engineRef.current.stop()
      queueRef.current = []
      busyRef.current = false
      setSpeaking(false)
    }
    setMuted((prev) => !prev)
  }, [muted])

  const speakText = useCallback(
    (text: string) => {
      if (muted || !text.trim()) return
      queueRef.current.push(text)
      processQueue()
    },
    [muted, processQueue],
  )

  const stopSpeaking = useCallback(() => {
    genRef.current++
    engineRef.current.stop()
    queueRef.current = []
    busyRef.current = false
    setSpeaking(false)
  }, [])

  const feedText = useCallback(
    (text: string) => {
      speakText(text)
    },
    [speakText],
  )

  useEffect(() => {
    return () => {
      genRef.current++
      engineRef.current.stop()
    }
  }, [])

  return {
    muted,
    speaking,
    toggleMute,
    speakText,
    stopSpeaking,
    feedText,
  }
}

import type { AnswerProfile, AnswerProfileOverride } from '@/api/types'

export const DEFAULT_ANSWER_PROFILE: Readonly<AnswerProfile> = Object.freeze({
  ensemble_enabled: false,
  models: [],
  referee_model: '',
  max_workers: 4,
  cache_enabled: true,
  cache_ttl_seconds: 7 * 24 * 60 * 60,
  course_context_enabled: true,
  web_search_enabled: false,
})

export function cloneAnswerProfile(profile: AnswerProfile): AnswerProfile {
  return {
    ...profile,
    models: [...profile.models],
  }
}

export function completeAnswerProfile(profile?: AnswerProfile | null): AnswerProfile {
  return cloneAnswerProfile(profile ?? DEFAULT_ANSWER_PROFILE)
}

export function mergeAnswerProfile(
  globalProfile: AnswerProfile,
  override: AnswerProfileOverride | null,
): AnswerProfile {
  return {
    ...cloneAnswerProfile(globalProfile),
    ...override,
    models: [...(override?.models ?? globalProfile.models)],
  }
}

export function normalizedAnswerProfile(profile: AnswerProfile): AnswerProfile {
  return {
    ...profile,
    models: [...new Set(profile.models.map((model) => model.trim()).filter(Boolean))],
    referee_model: profile.referee_model.trim(),
  }
}

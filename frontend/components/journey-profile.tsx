"use client";
import { createContext, useContext, useState } from 'react';
import type { TravelerProfile } from '@/lib/traveler-profile';
const ProfileContext = createContext<{ profile: TravelerProfile | null; remember: (profile: TravelerProfile) => void }>({profile:null,remember:()=>{}});
export function JourneyProfileProvider({children}: {children: React.ReactNode}) {
  const [profile,remember]=useState<TravelerProfile | null>(null);
  return <ProfileContext.Provider value={{profile,remember}}>{children}</ProfileContext.Provider>;
}
export function useJourneyProfile() { return useContext(ProfileContext); }

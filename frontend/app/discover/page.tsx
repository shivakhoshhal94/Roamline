import type { Metadata } from "next";
import { TravelerProfileForm } from "@/components/traveler-profile-form";

export const metadata: Metadata = {
  title: "Your travel preferences | Roamline",
};
export default function Discover() {
  return <TravelerProfileForm />;
}

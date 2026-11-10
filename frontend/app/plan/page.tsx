import { DestinationExplorer } from '@/components/destination-explorer';
export const metadata = { title: 'Explore a destination | Roamline' };
export default async function PlanPage({ searchParams }: { searchParams: Promise<{ city?: string }> }) {
  const params = await searchParams;
  return <DestinationExplorer initialCity={typeof params.city === 'string' ? params.city.slice(0, 80) : ''} />;
}

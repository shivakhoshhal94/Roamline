import Image from 'next/image';
export function DestinationImage({ destinationId }: { destinationId: number }) {
  if (destinationId !== 2950159) return null;
  return <figure className="destination-photo"><Image src="/destinations/berlin.jpg" alt="Brandenburg Gate in Berlin" width={960} height={665} sizes="(max-width: 700px) 90vw, 800px" /><figcaption><a href="https://commons.wikimedia.org/wiki/File:The_Brandenburg_Gate.jpg" target="_blank" rel="noreferrer">Nirmal Dulal / Wikimedia Commons</a> · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">CC BY-SA 4.0</a>. Resized, displayed cropped.</figcaption></figure>;
}

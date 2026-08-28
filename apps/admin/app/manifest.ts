import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Sagewai Work Control Console',
    short_name: 'Sagewai',
    description: 'Control and observe active Sagewai Work.',
    start_url: '/work',
    display: 'standalone',
    background_color: '#ffffff',
    theme_color: '#0f172a',
    icons: [
      {
        src: '/brand/sagewai_icon.svg',
        sizes: 'any',
        type: 'image/svg+xml',
      },
    ],
  };
}

// Phone / tablet vs PC, for UI that only makes sense on a handheld (the install banner).
// Not screen width: a narrow desktop window is still a PC, a big tablet is still a tablet.

export interface NavLike {
  userAgent: string;
  maxTouchPoints?: number;
  userAgentData?: { mobile?: boolean };
}

export interface Device {
  mobile: boolean;
  ios: boolean; // iPhone, iPod, iPad (iPadOS too): no beforeinstallprompt, Share > Add to Home Screen instead
}

// coarse: matchMedia("(pointer: coarse)").matches (a finger is the main pointer).
export function detectDevice(nav: NavLike, coarse: boolean): Device {
  const ua = nav.userAgent;
  const touch = (nav.maxTouchPoints ?? 0) > 1;
  // iPadOS Safari sends a desktop Mac UA; only the touch points give it away.
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touch);
  if (ios) return { mobile: true, ios: true };
  // Client hints say mobile:false on Android tablets, so Android counts on its own.
  const handheldUa = nav.userAgentData?.mobile === true || /Android|Mobi/i.test(ua);
  // A desktop browser with a mobile UA (extension, "request mobile site") still has a mouse.
  return { mobile: handheldUa && (coarse || touch), ios: false };
}

export function currentDevice(): Device {
  const coarse = typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;
  return detectDevice(navigator as NavLike, coarse);
}

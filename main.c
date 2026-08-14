#include <stdint.h>
#include "TrackerPlayer.h"
#include "WavetableSynth.h"
#include "Bsp.h"
#include "Protocol.h"

MEM_XDATA(TrackerPlayer) mainPlayer;

void main()
{
	HardwareInit();
	Proto_Init();
	WavetableSynthInit();
	AudioBufferInit();
	TrackerPlayerInit(&mainPlayer);
	TrackerPlayerStart(&mainPlayer, TRACKER_MODE_ORDER_PLAY);
	TrackerPlayerProcess(&mainPlayer);
	{
		uint16_t guard = 0;
		while (AudioBufferLevel() < 192 && guard < 4096) {
			AudioRenderProcess();
			guard++;
		}
	}
	StartAudioOutput();

	while (1)
	{
		TrackerPlayerProcess(&mainPlayer);
		VisualizeSound(AudioRenderProcess());
		Proto_Process();
	}
}

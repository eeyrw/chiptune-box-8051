#include <stdint.h>
#include "TrackerPlayer.h"
#include "WavetableSynth.h"
#include "Bsp.h"
#include "Protocol.h"
#include "Storage.h"

MEM_XDATA(TrackerPlayer) mainPlayer;

void main()
{
	HardwareInit();
	Proto_Init();
	WavetableSynthInit();
	AudioBufferInit();
	TrackerPlayerInit(&mainPlayer);
	storage_auto_detect();
	TrackerPlayerStart(&mainPlayer, TRACKER_MODE_ORDER_PLAY);
	TrackerPlayerProcess(&mainPlayer);
	while (AudioBufferLevel() < 192) AudioRenderProcess();
	StartAudioOutput();

	while (1)
	{
		TrackerPlayerProcess(&mainPlayer);
		VisualizeSound(AudioRenderProcess());
		Proto_Process();
	}
}

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
	TrackerPlayerInit(&mainPlayer);
	storage_auto_detect();
	TrackerPlayerStart(&mainPlayer, TRACKER_MODE_ORDER_PLAY);
	TrackerPlayerProcess(&mainPlayer);
	StartAudioOutput();

	while (1)
	{
		TrackerPlayerProcess(&mainPlayer);
		Proto_Process();
	}
}

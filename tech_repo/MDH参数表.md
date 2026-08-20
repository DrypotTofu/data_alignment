stSlaveMdhParam1 : ARRAY [G_Const.nRobotBaseStand_UB..G_Const.nRobotInstrument_UB] OF ST_MdhParam := [
		(q_type := EN_JointMotionType.Fixed,		a := 0, 	alpha := 0, 		d := 0, 		theta := 0), 			// joint 3
		(q_type := EN_JointMotionType.Revolute,		a := 160, 	alpha := 0, 		d := 0, 		theta := 90), 			// joint 4
		(q_type := EN_JointMotionType.Prismatic,	a := 63,	alpha := 90, 		d := 508, 		theta := 0), 			// joint 5
		(q_type := EN_JointMotionType.Prismatic,	a := 0, 	alpha := -90, 		d := 586, 		theta := 0), 			// joint 6
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 0, 		d := 0, 		theta := 0), 			// joint 7
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := -60,		d := 895, 		theta := 0), 			// joint 8
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 16, 		d := 0, 		theta := 90), 			// joint 9
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90, 		d := 0, 		theta := -136), 		// joint 10
		(q_type := EN_JointMotionType.Prismatic, 	a := 0, 	alpha := 90,		d := 10, 		theta := 0), 			// joint 11
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 0,			d := 0, 		theta := 0), 			// joint 12
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90,		d := 0, 		theta := 90), 			// joint 13
		(q_type := EN_JointMotionType.Revolute, 	a := 10.6, 	alpha := 90,		d := 0, 		theta := 0),			// joint 14/15
		(q_type := EN_JointMotionType.Fixed, 		a := 10.4, 	alpha := 0,			d := 0, 		theta := 0)]; 			// jaw length	



	// Slave Arm 2 MDH Parameters

	stSlaveMdhParam2 : ARRAY [G_Const.nRobotBaseStand_UB..G_Const.nRobotInstrument_UB] OF ST_MdhParam := [
		(q_type := EN_JointMotionType.Fixed,		a := 0, 	alpha := 0, 		d := 0, 		theta := 60), 			// joint 3
		(q_type := EN_JointMotionType.Revolute,		a := 160, 	alpha := 0, 		d  := 0, 		theta := 90), 			// joint 4
		(q_type := EN_JointMotionType.Prismatic,	a := 131,	alpha := 90, 		d := 378,		theta := 0), 			// joint 5
		(q_type := EN_JointMotionType.Prismatic,	a := 0, 	alpha := -90, 		d := 460, 		theta := 0), 			// joint 6
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 0, 		d := 0, 		theta := 0), 			// joint 7
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := -50,		d := 889, 		theta := 0), 			// joint 8
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 16, 		d := 0, 		theta := 90), 			// joint 9
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90, 		d := 0, 		theta := -146), 		// joint 10 -136
		(q_type := EN_JointMotionType.Prismatic, 	a := 0, 	alpha := 90,		d := 10, 		theta := 0), 			// joint 11
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 0,			d := 0, 		theta := 0), 			// joint 12
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90,		d := 0, 		theta := 90), 			// joint 13
		(q_type := EN_JointMotionType.Revolute, 	a := 10.6, 	alpha := 90,		d := 0, 		theta := 0),			// joint 14/15
		(q_type := EN_JointMotionType.Fixed, 		a := 10.4, 	alpha := 0,			d := 0, 		theta := 0)]; 			// jaw length

		

	// Slave Arm 3 MDH Parameters

	stSlaveMdhParam3 : ARRAY [G_Const.nRobotBaseStand_UB..G_Const.nRobotInstrument_UB] OF ST_MdhParam := [
		(q_type := EN_JointMotionType.Fixed,		a := 0, 	alpha := 0, 		d := 0, 		theta := 120), 			// joint 3
		(q_type := EN_JointMotionType.Revolute,		a := 160, 	alpha := 0, 		d  := 0, 		theta := 90), 			// joint 4
		(q_type := EN_JointMotionType.Prismatic,	a := 131,	alpha := 90, 		d := 376,		theta := 0), 			// joint 5
		(q_type := EN_JointMotionType.Prismatic,	a := 0, 	alpha := -90, 		d := 460, 		theta := 0), 			// joint 6
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 0, 		d := 0, 		theta := 0), 			// joint 7
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := -50,		d := 889, 		theta := 0), 			// joint 8
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 16, 		d := 0, 		theta := 90), 			// joint 9
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90, 		d := 0, 		theta := -146), 		// joint 10 -136
		(q_type := EN_JointMotionType.Prismatic, 	a := 0, 	alpha := 90,		d := 10, 		theta := 0), 			// joint 11
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 0,			d := 0, 		theta := 0), 			// joint 12
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90,		d := 0, 		theta := 90), 			// joint 13
		(q_type := EN_JointMotionType.Revolute, 	a := 10.6, 	alpha := 90,		d := 0, 		theta := 0),			// joint 14/15
		(q_type := EN_JointMotionType.Fixed, 		a := 10.4, 	alpha := 0,			d := 0, 		theta := 0)]; 			// jaw length
		

	// Slave Arm 4 MDH Parameters

	stSlaveMdhParam4 : ARRAY [G_Const.nRobotBaseStand_UB..G_Const.nRobotInstrument_UB] OF ST_MdhParam := [
		(q_type := EN_JointMotionType.Fixed,		a := 0, 	alpha := 0, 		d := 0, 		theta := 180), 			// joint 3
		(q_type := EN_JointMotionType.Revolute,		a := 160, 	alpha := 0, 		d := 0, 		theta := 90), 			// joint 4
		(q_type := EN_JointMotionType.Prismatic,	a := 202,	alpha := 90, 		d := 508, 		theta := 0), 			// joint 5
		(q_type := EN_JointMotionType.Prismatic,	a := 0, 	alpha := -90, 		d := 586, 		theta := 0), 			// joint 6
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 0, 		d := 0, 		theta := 0), 			// joint 7
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := -60,		d := 895, 		theta := 0), 			// joint 8
		(q_type := EN_JointMotionType.Revolute,		a := 0, 	alpha := 16, 		d := 0, 		theta := 90), 			// joint 9
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90, 		d := 0, 		theta := -136), 		// joint 10
		(q_type := EN_JointMotionType.Prismatic, 	a := 0, 	alpha := 90,		d := 10, 		theta := 0), 			// joint 11
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 0,			d := 0, 		theta := 0), 			// joint 12
		(q_type := EN_JointMotionType.Revolute, 	a := 0, 	alpha := 90,		d := 0, 		theta := 90), 			// joint 13
		(q_type := EN_JointMotionType.Revolute, 	a := 10.6, 	alpha := 90,		d := 0, 		theta := 0),			// joint 14/15
		(q_type := EN_JointMotionType.Fixed, 		a := 10.4, 	alpha := 0,			d := 0, 		theta := 0)]; 			// jaw length
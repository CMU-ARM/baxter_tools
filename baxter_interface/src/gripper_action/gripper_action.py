# Copyright (c) 2013, Rethink Robotics
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. Neither the name of the Rethink Robotics nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""
Baxter RSDK Gripper Action Server
"""
from math import fabs

import roslib
roslib.load_manifest('baxter_interface')
import rospy
import actionlib

from control_msgs.msg import (
    GripperCommandAction,
    GripperCommandFeedback,
    GripperCommandResult,
)

import dataflow
import baxter_interface

class GripperActionServer(object):
    def __init__(self, gripper, parameters):
        self._param = parameters
        self._ee = gripper + '_gripper'
        self._ns = 'robot/end_effector/' + self._ee + '/gripper_action'
        self._gripper = baxter_interface.Gripper(gripper)
        # Allow grippers to fully come up
        rospy.sleep(0.25)

        # Action Server
        self._server = actionlib.SimpleActionServer(
            self._ns,
            GripperCommandAction,
            execute_cb=self._on_gripper_action,
            auto_start=False)
        self._action_name = rospy.get_name()
        self._server.start()

        # Action Feedback/Result
        self._feedback = GripperCommandFeedback()
        self._result = GripperCommandResult()

        # Store Gripper Type
        self._type = self._gripper.type()

        # Verify Grippers Have No Errors and are Calibrated
        if self._gripper.error():
            self._gripper.reboot()
        if not self._gripper.calibrated():
            self._gripper.calibrate()

        # Initialize Parameters
        init_params = self._gripper.parameters()
        self._timeout = 5.0
        self._default_effort = 40.0
        if self._type == 'electric':
            self._dead_band = init_params['dead_zone']
            self._velocity = init_params['velocity']
            self._moving_force = init_params['moving_force']
            self._holding_force = init_params['holding_force']
#         elif self._type == 'suction':
#             self._suction = init_params['vacuum_sensor_threshold']
#             self._blow_off = init_params['blow_off_seconds']

    def _get_gripper_parameters(self):
        self._timeout = self._param.config[self._ee + '_timeout']
        if self._type == 'electric':
            self._dead_band = self._param.config[self._ee + '_goal']
            self._velocity = self._param.config[self._ee + '_velocity']
            self._moving_force = self._param.config[self._ee + '_moving_force']
            self._holding_force = self._param.config[self._ee + '_holding_force']
            param_update = dict({'dead_zone': self._dead_band,
                                 'velocity': self._velocity,
                                 'moving_force': self._moving_force,
                                 'holding_force': self._holding_force,
                                 })
            self._gripper.set_parameters(parameters=param_update)
#         if self._type == 'suction':
#             self._suction = self._param.config[self._ee + '_suction_threshold']
#             self._blow_off = self._param.config[self._ee + '_blow_off']
#             param_update = {'vacuum_suction_thresholf', self._suction,
#                             'blow_off_seconds', self._blow_off,
#                             }
#             self._gripper.set_parameters(parameters=param_update)

    def _update_feedback(self, position):
        self._feedback.position = self._gripper.position()
        self._feedback.effort = self._gripper.force()
        self._feedback.stalled = (self._gripper.force() >
                                  self._gripper.parameters()['moving_force'])
        if self._type == 'electric':
            self._feedback.reached_goal = (fabs(self._gripper.position() -
                                                position) < self._dead_band)
        if self._type == 'suction':
            self._gripper.gripping()
        self._result = self._feedback

    def _on_gripper_action(self, goal):
        position = goal.command.position
        effort = goal.command.max_effort
        # If effort not specified (0.0) set to default
        if fabs(effort) < 0.0001:
            effort = self._default_effort
        # Apply max effort if specified < 0
        if effort == -1.0:
            effort = 100.0;

        # Check for errors
        if self._gripper.error():
            rospy.logerr("%s: Gripper error - please restart action server." %
                         (self._action_name,))
            self._server.set_aborted()

        # Pull parameters that will define the gripper actuation
        self._get_gripper_parameters()

        # Reset feedback/result
        self._update_feedback(position)

        # 20 Hz gripper state rate
        control_rate = rospy.Rate(20.0)

        # Record start time
        start_time = rospy.get_time()

        # Set the moving_force/suction_threshold based on max_effort provided
        if self._type == 'electric':
            self._gripper.set_moving_force(effort)

        def now_from_start(start):
            return rospy.get_time() - start

        def check_state():
            if self._type == 'electric':
                return (self._gripper.force() >
                        self._gripper.parameters()['moving_force'] or
                        fabs(self._gripper.position() - position) <
                        self._dead_band)
#             if self._type == 'suction':
#                 return (self._gripper.gripping())

        # Continue commanding goal until success or timeout
        success = False
        while now_from_start(start_time) < self._timeout or self._timeout < 0.0:
            if self._server.is_preempt_requested():
                self._gripper.stop()
                rospy.loginfo("%s: Gripper Action Preempted" %
                              (self._action_name,))
            self._update_feedback(position)
            if check_state():
                self._server.set_succeeded(self._result)
                success = True
                return
            self._gripper.command_position(position, block=False)
            self._server.publish_feedback(self._feedback)
            control_rate.sleep()

        # Check failure state
        if not success:
            rospy.logerr("%s: Gripper Command Not Achieved in Allotted Time" %
                         (self._action_name,))
            self._update_feedback(position)
            self._server.set_aborted(self._result)
